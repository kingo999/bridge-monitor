import json
import unittest
from pathlib import Path
from bridge import admin
from bridge.local_worker.models import *
from bridge.local_worker.storage import WorkerStore
from tests.local_worker_helpers import TempWorld,packet_data


class Administration(unittest.TestCase):
    def test_group_exists_and_confirmation_is_required(self):
        parser=admin.build_parser()
        self.assertIn('local-worker',parser.format_help(),'LOCAL-00 administration missing')
        w=TempWorld();self.addCleanup(w.close)
        store=WorkerStore(w.root,(),w.home)
        request=GenerateRequest(action='generate',packet=TaskPacket(**packet_data()),attempt=1)
        with store.try_lock():store.begin_attempt(request,PreparedPrompt(text='x',prompt_hash='0'*64,proof_hash='1'*64,upper_bound=10),'a'*32)
        args=['local-worker','resolve-uncertain','--request-id','a'*32,'--reason','operator checked','--evidence-ref','operator observation']
        env={'USERPROFILE':str(w.home)}
        self.assertEqual(admin.main(args,env),2)
        self.assertEqual(store.read_snapshot().coordination,'uncertain')
        self.assertEqual(admin.main(args+['--confirmed-idle'],env),0)
        self.assertEqual(store.read_snapshot().coordination,'idle')
        self.assertEqual(admin.main(['local-worker','status','--json'],env),0)

    def test_status_does_not_require_codex_auth_or_repository(self):
        self.assertIn('local-worker',admin.build_parser().format_help(),'LOCAL-00 administration missing')
        w=TempWorld();self.addCleanup(w.close)
        self.assertEqual(admin.main(['local-worker','status','--json'],{'USERPROFILE':str(w.home),'GPT_ARCHITECT_MODEL':'INVALID MODEL'}),0)
