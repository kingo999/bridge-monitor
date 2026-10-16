import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from tests.local_worker_helpers import TempWorld, packet_data
from bridge.local_worker.models import WorkerError, TaskPacket, GenerateRequest, PreparedPrompt, LocalWorkerResult


class Storage(unittest.TestCase):
    def setUp(self):
        self.w=TempWorld();self.addCleanup(self.w.close)

    def store(self):
        try:
            from bridge.local_worker.storage import WorkerStore
        except ImportError:self.fail('shared worker store missing')
        return WorkerStore(self.w.root,(),self.w.home)

    def begin(self,store):
        req=GenerateRequest(action='generate',packet=TaskPacket(**packet_data()),attempt=1)
        prepared=PreparedPrompt(text='prompt',prompt_hash='0'*64,proof_hash='1'*64,upper_bound=20)
        return store.begin_attempt(req,prepared,'a'*32)

    def test_shared_lock_and_crash_marker_survive_restart(self):
        store=self.store()
        with store.try_lock():
            with self.assertRaises(WorkerError) as caught:
                with self.store().try_lock():pass
            self.assertEqual(caught.exception.code,'worker_busy')
            self.begin(store)
        other=self.store()
        self.assertEqual(other.read_snapshot().coordination,'uncertain')
        with other.try_lock(),self.assertRaises(WorkerError):self.begin(other)

    def test_other_process_cannot_acquire_held_lock(self):
        store=self.store()
        with store.try_lock():
            code='''from pathlib import Path
from bridge.local_worker.storage import WorkerStore
from bridge.local_worker.models import WorkerError
import sys
s=WorkerStore(Path(sys.argv[1]),(),Path(sys.argv[2]))
try:
 with s.try_lock(): print('WRONGLY_ENTERED')
except WorkerError as e: print(e.code)
'''
            r=subprocess.run([sys.executable,'-B','-c',code,str(self.w.root),str(self.w.home)],capture_output=True,text=True,timeout=8)
            self.assertEqual(r.stdout.strip(),'worker_busy',r.stderr)

    def test_hard_kill_releases_os_lock_but_preserves_active_marker(self):
        import time
        self.store()  # Assert feature availability before starting the child.
        code='''from pathlib import Path
import sys,time
from bridge.local_worker.storage import WorkerStore
from bridge.local_worker.models import TaskPacket,GenerateRequest,PreparedPrompt
from tests.local_worker_helpers import packet_data
s=WorkerStore(Path(sys.argv[1]),(),Path(sys.argv[2]))
with s.try_lock():
 s.begin_attempt(GenerateRequest(action='generate',packet=TaskPacket(**packet_data()),attempt=1),PreparedPrompt(text='p',prompt_hash='0'*64,proof_hash='1'*64,upper_bound=20),'a'*32)
 time.sleep(30)
'''
        process=subprocess.Popen([sys.executable,'-B','-c',code,str(self.w.root),str(self.w.home)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            while not (self.w.root/'active.json').exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(.01)
            self.assertTrue((self.w.root/'active.json').exists())
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
        store=self.store()
        self.assertEqual(store.read_snapshot().coordination,'uncertain')
        with store.try_lock():
            with self.assertRaises(WorkerError):self.begin(store)

    def test_terminal_ledger_before_marker_retirement_and_replay(self):
        store=self.store()
        with store.try_lock():
            self.begin(store)
            store.finish_attempt('a'*32,LocalWorkerResult(action='generate',state='draft_returned',code='ok',response='answer'))
        self.assertEqual(store.read_snapshot().coordination,'idle')
        self.assertEqual(store.read_task('sample').attempts['1'].result.response,'answer')
        taskpath=next((self.w.root/'tasks').glob('*.json'));taskpath.unlink()
        with self.assertRaises(WorkerError):store.read_task('sample')

    def test_corruption_and_repository_overlap_refuse(self):
        store=self.store()
        with store.try_lock():pass
        (self.w.root/'snapshot.json').write_text('{')
        with self.assertRaises(WorkerError):store.read_snapshot()
        from bridge.local_worker.storage import WorkerStore
        with self.assertRaises(WorkerError):
            with WorkerStore(self.w.root,(self.w.home,),self.w.home).try_lock():pass

    def test_operator_resolution_requires_matching_confirmation(self):
        store=self.store()
        with store.try_lock():
            self.begin(store)
            for request,ack in [('a'*32,False),('b'*32,True)]:
                with self.assertRaises(WorkerError):store.resolve_uncertain(request,ack,'checked','operator-ref')
            store.resolve_uncertain('a'*32,True,'server confirmed idle','operator-ref')
        self.assertEqual(store.read_snapshot().coordination,'idle')
        self.assertTrue(store.read_task('sample').attempts['1'].retired)

    def test_resolution_clears_persisted_busy_snapshot(self):
        store=self.store()
        with store.try_lock():
            self.begin(store)
            snapshot=store.read_snapshot();store.write('snapshot.json',snapshot)
            store.resolve_uncertain('a'*32,True,'confirmed idle','operator')
        self.assertEqual(store.read_snapshot().coordination,'idle')
