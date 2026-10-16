"""One locked, audited POST; uncertain delivery never becomes an automatic retry."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time
import uuid
from .models import *
from .settings import MODEL,DIGEST,PROFILE,load_worker_settings
from .prompts import PromptBudgeter,baseline_proof,verify_baseline
from .audit import AuditWriter
from .service import check_tags

METRICS=('prompt_eval_count','eval_count','prompt_eval_cached_count','total_duration','load_duration','prompt_eval_duration','eval_duration')


def completed_draft(data,prepared):
    if not isinstance(data,dict):raise WorkerError('completion_incomplete')
    for key in METRICS:
        value=data.get(key)
        if value is not None and (type(value) is not int or value<0):raise WorkerError('metrics_invalid')
    if data.get('prompt_eval_count') is None:raise WorkerError('prompt_count_missing')
    if data['prompt_eval_count']>prepared.upper_bound:raise WorkerError('prompt_budget_contradiction')
    if data.get('model')!=MODEL or data.get('done') is not True or data.get('done_reason')!='stop' or not isinstance(data.get('response'),str) or not data['response'].strip():
        raise WorkerError('completion_incomplete')
    if data['response'].lstrip().startswith('NEEDS_CONTEXT:'):raise WorkerError('worker_clarification')


class GenerationRunner:
    def __init__(self,store,transport,budgeter=None,audit=None):
        self.store=store;self.transport=transport
        self.budgeter=budgeter or PromptBudgeter();self.audit=audit or AuditWriter(store)

    async def run(self,request,endpoint,proof=None,*,selection_revision=None):
        started=time.monotonic();request_id=uuid.uuid4().hex
        executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='local00-store')
        loop=asyncio.get_running_loop();lease=None;acquired=False;begun=False;post_possible=False;terminal=False;validated=False
        refs=[];outcome=None;prepared=None
        result=LocalWorkerResult(action='generate',state='unavailable',code='not_started',task_id=request.packet.task_id,
            packet_hash=canonical_packet_hash(request.packet),request_id=request_id,attempt=request.attempt,endpoint=endpoint.base_url,model=MODEL)

        async def io(fn,*args):
            future=loop.run_in_executor(executor,partial(fn,*args))
            try:return await asyncio.shield(future)
            except asyncio.CancelledError:
                await asyncio.shield(future)
                raise

        async def exchange(path,method='GET',body=None,timeout=5.0,limit=1048576):
            return await self.transport.exchange(TransportRequest(endpoint=endpoint,method=method,path=path,body=body,timeout_s=timeout,max_bytes=limit))

        try:
            proof=proof or baseline_proof()
            # A preliminary bound runs before the lease; retry preparation adds recorded feedback below.
            self.budgeter.prepare(request.packet,proof)
            lease=self.store.try_lock();await io(lease.__enter__);acquired=True
            settings=await io(load_worker_settings,self.store.root)
            task=await io(self.store.read_task,request.packet.task_id)
            if task:
                if task.packet_hash!=result.packet_hash:raise WorkerError('task_conflict')
                previous=task.attempts.get(str(request.attempt))
                if previous:
                    if previous.result is not None:
                        return previous.result.model_copy(update={'replayed_from_ledger':True})
                    raise WorkerError('attempt_incomplete')
            if await io(self.store.active):raise WorkerError('worker_uncertain')
            snapshot=await io(self.store.read_snapshot)
            if ((snapshot.endpoint is not None and snapshot.endpoint!=endpoint)
                    or (selection_revision is not None and (snapshot.availability!='available'
                        or snapshot.connection_revision!=selection_revision or snapshot.endpoint!=endpoint))):
                raise WorkerError('endpoint_selection_changed')
            if snapshot.budget_block:raise WorkerError('budget_blocked')
            feedback=None;prior=None
            if request.attempt==2 or (task and task.validations):
                from .validation import retry_feedback
                feedback=retry_feedback(task,request)
                if feedback:prior=task.attempts['1'].result.response
            prepared=self.budgeter.prepare(request.packet,proof,feedback,prior)
            tags=await exchange('/api/tags',timeout=settings.discovery_timeout_s)
            check_tags(tags);validated=True;result.digest=DIGEST;result.peer_ip=tags.peer_ip
            version=await exchange('/api/version',timeout=settings.discovery_timeout_s)
            metadata=await exchange('/api/show','POST',{'model':MODEL},settings.discovery_timeout_s)
            if version.status!=200 or version.code!='ok' or metadata.status!=200 or metadata.code!='ok':raise WorkerError('budget_unverifiable')
            current_proof=verify_baseline(metadata.data,version.data.get('version'))
            if current_proof!=proof:raise WorkerError('budget_unverifiable')
            prepared=self.budgeter.prepare(request.packet,current_proof,feedback,prior)
            resident=await exchange('/api/ps',timeout=settings.discovery_timeout_s)
            rows=resident.data.get('models') if resident.data else None
            if resident.status!=200 or resident.code!='ok' or not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise WorkerError('resident_models_invalid')
            if any(row.get('name')!=MODEL or row.get('digest')!=DIGEST for row in rows):raise WorkerError('resident_model_busy')
            wire={**PROFILE,'options':dict(PROFILE['options']),'prompt':prepared.text}
            await io(self.store.begin_attempt,request,prepared,request_id);begun=True
            refs.append(await io(self.audit.write_request,request_id,wire,request.packet,prepared))
            check_elapsed=time.monotonic()-started
            post_possible=True
            outcome=await exchange('/api/generate','POST',wire,settings.generation_timeout_s,4194304)
            refs.append(await io(self.audit.write_response,request_id,outcome))
            terminal=outcome.phase=='terminal_received' and outcome.data is not None and outcome.data.get('done') is True
            if outcome.code!='ok' or outcome.status!=200:
                raise WorkerError(outcome.code if outcome.code!='ok' else 'generation_http_error')
            data=outcome.data or {}
            # Preserve completed response and metrics even when semantic/format checks reject it.
            result.peer_ip=outcome.peer_ip
            if isinstance(data.get('response'),str):result.response=data['response']
            result.done=data.get('done') if type(data.get('done')) is bool else None
            result.done_reason=data.get('done_reason') if isinstance(data.get('done_reason'),str) else None
            result.result_digest=content_hash(data)
            result.metrics={key:data.get(key) if type(data.get(key)) is int else None for key in METRICS}
            for key in METRICS:
                if key.endswith('_duration'):
                    value=result.metrics[key];result.metrics[key+'_s']=value/1_000_000_000 if value is not None else None
            completed_draft(data,prepared)
            result.state='draft_returned';result.code='draft_returned';result.availability='available'
            result.timings={'checks_s':check_elapsed,'inference_s':outcome.elapsed_s,'client_elapsed_s':time.monotonic()-started}
        except asyncio.CancelledError:
            if acquired and begun:
                try:await io(self.store.mark_uncertain,request_id,'cancelled_after_marker')
                except (WorkerError,OSError):pass
            raise
        except (WorkerError,OSError,ValueError) as exc:
            code=exc.code if isinstance(exc,WorkerError) else 'audit_or_state_error'
            result.code=code
            result.state='busy' if code in {'worker_busy','resident_model_busy'} else ('incomplete_error' if begun else 'unavailable')
            result.needs_context=code=='worker_clarification'
            result.coordination='uncertain' if code in {'worker_uncertain','attempt_incomplete'} or (begun and post_possible and not terminal) else ('busy' if result.state=='busy' else 'idle')
            result.timings['client_elapsed_s']=time.monotonic()-started
            if code in {'prompt_budget_contradiction','prompt_count_missing'} and acquired:
                try:
                    snapshot=await io(self.store.read_snapshot);snapshot.budget_block=code
                    await io(self.store.write,'snapshot.json',snapshot)
                except (OSError,WorkerError):pass
        finally:
            if acquired:
                if begun:
                    try:
                        result.audit_refs=refs
                        await io(self.audit.write_metadata,request_id,{'task_id':request.packet.task_id,'attempt':request.attempt,
                            'state':result.state,'code':result.code,'timings':result.timings,'effort':request.effort.model_dump(mode='json'),
                            'first_pass_acceptance':None,'retry_outcome':None,'supervisor_edits':None})
                        if post_possible and not terminal:
                            await io(self.store.mark_uncertain,request_id,result.code)
                            result.coordination='uncertain'
                        else:
                            await io(self.store.finish_attempt,request_id,result)
                    except (OSError,WorkerError,ValueError):
                        result.state='incomplete_error';result.code='audit_or_state_error';result.coordination='uncertain'
                await io(lease.__exit__,None,None,None)
            executor.shutdown(wait=True)
        return result
