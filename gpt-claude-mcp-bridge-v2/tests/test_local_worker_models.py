import unittest
from pydantic import ValidationError
from tests.local_worker_helpers import packet_data


class Contracts(unittest.TestCase):
    def api(self):
        try:
            from bridge.local_worker import models, settings
        except ImportError:
            self.fail('local worker contracts are missing')
        return models, settings

    def test_addresses_and_fixed_settings(self):
        m, s = self.api()
        self.assertEqual(s.parse_address(' skynet.local ').base_url, 'http://skynet.local:11434')
        self.assertEqual(s.parse_address('[2001:db8::78]').base_url, 'http://[2001:db8::78]:11434')
        for value in ['http://x', 'x:11434', '0.0.0.0', '127.0.0.1', '::1', '::', '224.0.0.1', 'a/b', 'a@b', 'a?b', 'fe80::1%1', '123.456.0.1']:
            with self.subTest(value=value), self.assertRaises(m.WorkerError): s.parse_address(value)
        self.assertEqual(s.PROFILE['options'], dict(num_ctx=16384, num_predict=2048, temperature=0))

    def test_strict_packets_and_action_union(self):
        m, s = self.api()
        p = m.TaskPacket(**packet_data())
        for extra in [dict(attempt=True), dict(attempt=3), dict(attempt=1, raw=True),dict(attempt=1,schema_version=True)]:
            with self.assertRaises(ValidationError):
                m.REQUEST_ADAPTER.validate_python(dict(action='generate',packet=p,**extra))
        self.assertEqual(m.REQUEST_ADAPTER.validate_python(dict(action='status')).action, 'status')
        with self.assertRaises(ValidationError):m.REQUEST_ADAPTER.validate_python(dict(action='status', address='x'))
        for change in [dict(category='architecture'),dict(checks=[]),dict(source=''),dict(task_id='../escape'),dict(objective='x'*9000),dict(approval={'classification':'synthetic','approval_ref':'x','audit_content_approved':False})]:
            with self.subTest(change=list(change)),self.assertRaises(ValidationError):m.TaskPacket(**(packet_data()|change))
        self.assertNotEqual(m.canonical_packet_hash(p),m.canonical_packet_hash(m.TaskPacket(**(packet_data()|{'source':'different'}))))

    def test_settings_timeouts_and_shared_root(self):
        m, s = self.api()
        from tests.local_worker_helpers import TempWorld
        import json
        w=TempWorld(); self.addCleanup(w.close);w.root.mkdir()
        for value in [True, float('nan'), 0, 16]:
            (w.root/'config.json').write_text(json.dumps({'schema_version':1,'discovery_timeout_s':value}))
            with self.assertRaises(m.WorkerError): s.load_worker_settings(w.root)
        self.assertEqual(s.worker_root({'USERPROFILE':str(w.home),'GPT_ARCHITECT_V2_STATE_DIR':'a'}),s.worker_root({'USERPROFILE':str(w.home),'GPT_ARCHITECT_V2_STATE_DIR':'b'}))
