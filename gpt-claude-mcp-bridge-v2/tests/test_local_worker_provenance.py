import tempfile
import unittest
from pathlib import Path
from tools.record_evidence import source_files, fingerprint
from tools.make_verification_prompt import expand_full, _source_diff


class Provenance(unittest.TestCase):
    def test_nested_code_and_fixtures_are_hashed_and_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / 'bridge/local_worker/transport.py'
            fixture = root / 'tests/fixtures/local_worker/tags.json'
            for p, text in ((code, 'VALUE = 1\n'), (fixture, '{}')):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            names = [p.relative_to(root).as_posix() for p in source_files(root)]
            self.assertIn('bridge/local_worker/transport.py', names)
            self.assertIn('tests/fixtures/local_worker/tags.json', names)
            before = fingerprint(root)['combined_sha256']
            code.write_text('VALUE = 2\n')
            self.assertNotEqual(before, fingerprint(root)['combined_sha256'])
            self.assertIn('bridge/local_worker/transport.py', expand_full(root))
            self.assertIn('VALUE = 2', _source_diff(root, root / 'empty'))
