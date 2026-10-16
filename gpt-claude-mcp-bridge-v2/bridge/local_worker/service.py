"""Optional session control and bounded discovery, isolated from ArchitectService."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
from ..state import utc_now
from .models import *
from .settings import *
from .storage import WorkerStore
from .transport import WorkerTransport


def check_tags(outcome):
    if outcome.code!='ok' or outcome.status!=200 or not isinstance(outcome.data,dict):
        raise WorkerError(outcome.code if outcome.code!='ok' else 'tags_http_error')
    rows=outcome.data.get('models')
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise WorkerError('tags_invalid')
    matches=[row for row in rows if row.get('name')==MODEL]
    if len(matches)!=1:raise WorkerError('model_missing_or_duplicate')
    if matches[0].get('digest')!=DIGEST:raise WorkerError('model_digest_mismatch')


class WorkerService:
    def __init__(self,env=None,store=None,transport=None,runner=None,recorder=None):
        self.env=dict(os.environ if env is None else env)
        home=Path(self.env.get('USERPROFILE') or Path.home())
        repos=(Path(self.env.get('CLAUDE_PROJECT_DIR') or Path.cwd()),)
        self.store=store or WorkerStore(worker_root(self.env),repos,home)
        self.transport=transport or WorkerTransport()
        self.runner=runner
        self.recorder=recorder
        self.disabled=False
        self._revision=0
        self._started=False
        self._discovering=False
        self._discovery_lock=asyncio.Lock()

    def settings(self):
        self.store.validate(self.store.root/'config.json')
        return load_worker_settings(self.store.root)

    def status(self,action='status',code='worker_status'):
        try:
            snapshot=self.store.read_snapshot()
        except WorkerError as exc:
            if not self._discovering or exc.code!='worker_initialization_incomplete':raise
            snapshot=WorkerSnapshot()
        availability='disabled' if self.disabled else ('discovering' if self._discovering else snapshot.availability)
        return LocalWorkerResult(action=action,state='status_returned',code=code,
            availability=availability,coordination=snapshot.coordination,
            endpoint=snapshot.endpoint.base_url if snapshot.endpoint else None,peer_ip=snapshot.peer_ip,
            model=MODEL,digest=DIGEST if snapshot.endpoint else None,
            explanation=CONTINUE if self.disabled else (RECOVERY if availability=='unavailable' else ''),
            next_actions=CHOICES if availability in {'unavailable','disabled'} else [])

    async def startup(self):
        if self._started:return
        self._started=True
        try:await self.discover(action_revision=0,explicit=False)
        except (WorkerError,OSError,ValueError):pass  # optional worker never aborts MCP lifespan

    async def discover(self,address=None,*,action_revision=None,explicit=True):
        revision=self._revision if action_revision is None else action_revision
        async with self._discovery_lock:
            self._discovering=True
            try:
                return await self._discover(address,revision,explicit)
            finally:
                self._discovering=False

    async def _discover(self,address,revision,explicit):
        settings=await asyncio.to_thread(self.settings)
        candidates=[parse_address(address)] if address is not None else [parse_address(HOSTNAME)]
        fallback=settings.last_validated_ip or settings.initial_ip
        if address is None and fallback:candidates.append(parse_address(fallback))
        def reserve():
            with self.store.try_lock():
                snapshot=self.store.read_snapshot()
                snapshot.connection_revision+=1
                self.store.write('snapshot.json',snapshot)
                return snapshot.connection_revision
        connection_revision=await asyncio.to_thread(reserve)
        self._discovering=True
        results=[];selected=None;peer=None
        try:
            for endpoint in candidates:
                outcome=await self.transport.exchange(TransportRequest(endpoint=endpoint,method='GET',path='/api/tags',timeout_s=settings.discovery_timeout_s))
                try:
                    check_tags(outcome)
                    # The actual connected peer, never an arbitrary resolver result.
                    parse_address(outcome.peer_ip or '')
                except WorkerError as exc:
                    results.append({'candidate':endpoint.base_url,'code':exc.code})
                else:
                    results.append({'candidate':endpoint.base_url,'code':'validated','peer_ip':outcome.peer_ip})
                    selected,peer=endpoint,outcome.peer_ip
                    break
            def persist():
                with self.store.try_lock():
                    snapshot=self.store.read_snapshot()
                    if snapshot.connection_revision!=connection_revision:return False
                    snapshot.availability='available' if selected else 'unavailable'
                    snapshot.endpoint=selected
                    snapshot.peer_ip=peer
                    snapshot.candidate_results=results
                    if selected:
                        current=self.settings()
                        current.last_validated_ip=peer
                        current.validated_at=utc_now();current.provenance='validated_actual_peer'
                        self.store.write('config.json',current)
                    self.store.write('snapshot.json',snapshot)
                    return True
            current=await asyncio.to_thread(persist)
            if address is not None and not selected:
                explanation=f'LOCAL-00 could not be validated at {candidates[0].base_url}: {results[-1]["code"]}. Enter another address, retry discovery, or continue without LOCAL-00.'
            else:explanation=RECOVERY
        finally:self._discovering=False
        if selected and current and explicit and revision==self._revision:self.disabled=False
        result=await asyncio.to_thread(self.status,'reconnect','validated' if selected and current else 'worker_unavailable')
        if not selected:result.explanation=explanation
        return result

    async def handle(self,request):
        try:
            if request.action=='status':return await asyncio.to_thread(self.status)
            if request.action=='continue_without':
                self._revision+=1;self.disabled=True
                return await asyncio.to_thread(self.status,request.action,'continued_without')
            if request.action=='reconnect':
                self._revision+=1
                return await self.discover(request.address,action_revision=self._revision,explicit=True)
            if request.action=='record_validation':
                if self.recorder is None:
                    from .validation import ValidationRecorder
                    self.recorder=ValidationRecorder(self.store)
                return await asyncio.to_thread(self.recorder.record,request.report)
            if request.action=='generate':
                status=await asyncio.to_thread(self.status)
                if status.availability!='available':
                    return status.model_copy(update={'action':'generate','state':'busy' if status.availability=='discovering' else 'unavailable','code':'worker_'+status.availability})
                if self.runner is None:
                    from .generation import GenerationRunner
                    self.runner=GenerationRunner(self.store,self.transport)
                snapshot=await asyncio.to_thread(self.store.read_snapshot)
                if snapshot.availability!='available' or snapshot.endpoint is None:
                    raise WorkerError('endpoint_selection_changed')
                return await self.runner.run(request,snapshot.endpoint,selection_revision=snapshot.connection_revision)
            raise WorkerError('unknown_action')
        except WorkerError as exc:
            return LocalWorkerResult(action=request.action,state='busy' if exc.code=='worker_busy' else 'unavailable',code=exc.code,
                explanation=RECOVERY,next_actions=CHOICES,availability='disabled' if self.disabled else 'unavailable')
        except (OSError,ValueError):
            return LocalWorkerResult(action=request.action,state='unavailable',code='worker_state_error',explanation=RECOVERY,next_actions=CHOICES)

    async def aclose(self):
        await self.transport.aclose()
