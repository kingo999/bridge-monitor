"""Shared worker transactions. A durable active marker outlives the OS lock."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from ..state import FileLock, LockTimeout, atomic_write_text, utc_now
from .models import (WorkerError, WorkerSnapshot, TaskRecord, AttemptRecord,
                     ActiveRequest, LocalWorkerResult, canonical_json, canonical_packet_hash)


def overlaps(a: Path, b: Path) -> bool:
    a,b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


class WorkerStore:
    def __init__(self, root: Path, repo_roots: tuple[Path,...], home: Path):
        self.root=Path(root)
        self.repo_roots=repo_roots
        self.home=Path(home)
        self._owner=None

    def validate(self, path: Path | None = None):
        target=path or self.root
        for part in (target,*target.parents):
            try:
                info=part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise WorkerError('worker_path_reparse')
        if overlaps(self.root,self.home/'.gpt-architect-bridge') or any(overlaps(self.root,p) for p in self.repo_roots):
            raise WorkerError('worker_path_overlap')
        if target != self.root and self.root.resolve() not in target.resolve().parents:
            raise WorkerError('worker_path_escape')

    def path(self, relative: str) -> Path:
        path=self.root/relative
        self.validate(path)
        return path

    def read(self, relative: str, limit: int=33554432):
        p=self.path(relative)
        try:
            if not p.is_file() or p.stat().st_size>limit:
                raise ValueError('invalid bounded record')
            return json.loads(p.read_text(encoding='utf-8'))
        except (ValueError,OSError) as exc:
            raise WorkerError('worker_state_invalid') from exc

    def write(self, relative: str, data):
        self.require_lease()
        p=self.path(relative)
        p.parent.mkdir(parents=True,exist_ok=True)
        text=canonical_json(data)
        if len(text.encode())>33554432:
            raise WorkerError('worker_record_too_large')
        atomic_write_text(p,text)

    def require_lease(self):
        if self._owner != threading.get_ident():
            raise WorkerError('worker_lease_required')

    def _establish(self):
        witness=self.path('instance.json')
        if not witness.exists():
            # A partially initialized directory is never silently recycled.
            if any(p.name not in {'worker.lock','config.json'} for p in self.root.iterdir()):
                raise WorkerError('worker_initialization_incomplete')
            if self.path('config.json').exists():
                from .settings import load_worker_settings
                load_worker_settings(self.root)
            self.write('index.json',{'schema_version':1,'tasks':{}})
            self.write('snapshot.json',WorkerSnapshot())
            self.write('instance.json',{'schema_version':1,'established_at':utc_now()})
        witness_data=self.read('instance.json',16384)
        if (not isinstance(witness_data,dict) or set(witness_data)!={'schema_version','established_at'}
                or type(witness_data['schema_version']) is not int or witness_data['schema_version']!=1
                or not isinstance(witness_data['established_at'],str) or not witness_data['established_at'].strip()):
            raise WorkerError('worker_state_invalid')
        self._index()

    def _index(self):
        data=self.read('index.json',1048576)
        if not isinstance(data,dict) or set(data)!={'schema_version','tasks'} or data['schema_version']!=1 or not isinstance(data['tasks'],dict):
            raise WorkerError('worker_state_invalid')
        for key,name in data['tasks'].items():
            if not isinstance(key,str) or len(key)!=64 or name!=f'tasks/{key}.json' or not self.path(name).is_file():
                raise WorkerError('worker_ledger_missing')
        return data

    @contextmanager
    def try_lock(self):
        self.validate()
        self.root.mkdir(parents=True,exist_ok=True)
        lock=FileLock(self.path('worker.lock'))
        try:
            lock.acquire(0)
        except LockTimeout:
            raise WorkerError('worker_busy') from None
        previous=self._owner
        self._owner=threading.get_ident()
        try:
            self._establish()
            yield self
        finally:
            self._owner=previous
            lock.release()

    def read_snapshot(self):
        self.validate()
        if not self.root.exists():
            return WorkerSnapshot()
        if not self.path('instance.json').exists():
            raise WorkerError('worker_initialization_incomplete')
        self._index()
        try:
            snapshot=WorkerSnapshot.model_validate(self.read('snapshot.json',262144))
        except ValueError as exc:raise WorkerError('worker_state_invalid') from exc
        if self.path('active.json').exists():
            probe=FileLock(self.path('worker.lock'))
            try:
                probe.acquire(0)
            except LockTimeout:
                snapshot.coordination='busy'
            else:
                probe.release()
                snapshot.coordination='uncertain'
        return snapshot

    def read_task(self,task_id):
        if not self.root.exists():return None
        key=hashlib.sha256(task_id.encode()).hexdigest()
        index=self._index()
        if key not in index['tasks']:return None
        try:return TaskRecord.model_validate(self.read(index['tasks'][key]))
        except ValueError as exc:raise WorkerError('worker_ledger_invalid') from exc

    def save_task(self,task):
        self.require_lease()
        key=hashlib.sha256(task.packet.task_id.encode()).hexdigest()
        relative=f'tasks/{key}.json'
        index=self._index()
        self.write(relative,task)
        if key not in index['tasks']:
            index['tasks'][key]=relative
            self.write('index.json',index)

    def active(self):
        if not self.path('active.json').exists():return None
        try:return ActiveRequest.model_validate(self.read('active.json',16384))
        except ValueError as exc:raise WorkerError('worker_active_invalid') from exc

    def begin_attempt(self,request,prepared,request_id):
        self.require_lease()
        if self.active():raise WorkerError('worker_uncertain')
        task=self.read_task(request.packet.task_id)
        packet_hash=canonical_packet_hash(request.packet)
        if task and task.packet_hash!=packet_hash:raise WorkerError('task_conflict')
        task=task or TaskRecord(packet=request.packet,packet_hash=packet_hash)
        if str(request.attempt) in task.attempts:raise WorkerError('attempt_exists')
        attempt=AttemptRecord(request_id=request_id,attempt=request.attempt)
        task.attempts[str(request.attempt)]=attempt
        self.save_task(task)
        self.write('active.json',ActiveRequest(request_id=request_id,task_id=request.packet.task_id,attempt=request.attempt))
        return attempt

    def finish_attempt(self,request_id,result):
        self.require_lease()
        active=self.active()
        if not active or active.request_id!=request_id:raise WorkerError('request_conflict')
        task=self.read_task(active.task_id)
        task.attempts[str(active.attempt)].result=result
        self.save_task(task)
        self.path('active.json').unlink()
        snapshot=self.read_snapshot();snapshot.coordination='idle'
        self.write('snapshot.json',snapshot)

    def mark_uncertain(self,request_id,code):
        active=self.active()
        if not active or active.request_id!=request_id:raise WorkerError('request_conflict')
        active.code=code
        self.write('active.json',active)

    def append_validation(self,report):
        self.require_lease()
        task=self.read_task(report.task_id)
        if task is None:raise WorkerError('task_missing')
        if report not in task.validations:
            task.validations.append(report)
            self.save_task(task)
        return task

    def resolve_uncertain(self,request_id,confirmed_idle,reason,evidence_ref):
        self.require_lease()
        if not confirmed_idle or not reason.strip() or not evidence_ref.strip():raise WorkerError('operator_confirmation_required')
        active=self.active()
        if not active or active.request_id!=request_id:raise WorkerError('request_conflict')
        task=self.read_task(active.task_id)
        attempt=task.attempts[str(active.attempt)]
        attempt.retired=True
        if attempt.result is None:
            attempt.result=LocalWorkerResult(action='generate',state='incomplete_error',code='operator_retired',request_id=request_id,task_id=active.task_id,attempt=active.attempt)
        self.save_task(task)
        self.write(f'resolutions/{request_id}.json',{'request_id':request_id,'reason':reason,'evidence_ref':evidence_ref,'confirmed_at':utc_now()})
        self.path('active.json').unlink()
        snapshot=self.read_snapshot();snapshot.coordination='idle'
        self.write('snapshot.json',snapshot)
