import importlib.util
from pathlib import Path
import unittest


class LiveRunner(unittest.TestCase):
    def load(self):
        path=Path(__file__).resolve().parents[1]/'tools/local_worker_live_check.py'
        self.assertTrue(path.exists(),'explicit live checker missing')
        spec=importlib.util.spec_from_file_location('live_check',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_generation_requires_all_approvals_and_stable_task_id(self):
        module=self.load()
        for args in [['--generate-synthetic'],['--generate-synthetic','--source-approved'],['--generate-synthetic','--source-approved','--audit-approved']]:
            with self.assertRaises(SystemExit) as caught:module.parse_args(args)
            self.assertEqual(caught.exception.code,2)

    def test_default_is_metadata_only_and_no_candidate_exec(self):
        module=self.load();args=module.parse_args([])
        self.assertFalse(args.generate_synthetic)
        packet=module.pilot_packet('pilot-check')
        self.assertEqual(packet['approval']['classification'],'synthetic')
        self.assertEqual(packet['task_id'],'pilot-check')
