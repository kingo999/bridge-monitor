import json
from pathlib import Path
import unittest
from bridge.local_worker.models import TaskPacket,WorkerError,ValidationReport
from tests.local_worker_helpers import packet_data


class Budget(unittest.TestCase):
    def api(self):
        try:from bridge.local_worker import prompts
        except ImportError:self.fail('verified baseline prompt budget missing')
        return prompts

    def test_verified_metadata_and_version_only(self):
        p=self.api();data=json.loads((Path(__file__).parent/'fixtures/local_worker/baseline_metadata.json').read_text())
        proof=p.verify_baseline(data,'0.30.9')
        self.assertEqual(proof.method,'verified_utf8_bpe')
        for changed,version in [(data,'different'),(data|{'system':'altered'},'0.30.9'),(data|{'template':'{{ custom }}'},'0.30.9')]:
            with self.assertRaises(WorkerError):p.verify_baseline(changed,version)

    def test_prompt_preserves_contract_and_bounds_utf8(self):
        p=self.api();proof=p.baseline_proof()
        packet=TaskPacket(**packet_data())
        prepared=p.PromptBudgeter().prepare(packet,proof)
        self.assertIn(packet.source,prepared.text)
        self.assertIn('NEEDS_CONTEXT:',prepared.text)
        self.assertLessEqual(prepared.upper_bound,14336)
        changed=TaskPacket(**(packet_data()|{'source':'é'*8000}))
        with self.assertRaises(WorkerError):p.PromptBudgeter().prepare(changed,proof)

    def test_retry_includes_exact_failure_and_cannot_silently_truncate(self):
        p=self.api();packet=TaskPacket(**packet_data())
        report=ValidationReport(task_id='sample',attempt=1,packet_hash='0'*64,result_digest='1'*64,verdict='failed',observations=[],diagnostics='input None: expected [], actual TypeError')
        text=p.render_task(packet,report,'bad code')
        self.assertIn(report.diagnostics,text);self.assertIn(packet.constraints,text)
        with self.assertRaises(WorkerError):p.PromptBudgeter().prepare(packet,p.baseline_proof(),report,'x'*15000)

    def test_complete_14336_14337_boundary(self):
        p=self.api();proof=p.baseline_proof();packet=TaskPacket(**packet_data())
        current=p.PromptBudgeter().prepare(packet,proof).upper_bound
        source=packet.source+'a'*(14336-current)
        at_limit=packet.model_copy(update={'source':source})
        self.assertEqual(p.PromptBudgeter().prepare(at_limit,proof).upper_bound,14336)
        with self.assertRaises(WorkerError):p.PromptBudgeter().prepare(at_limit.model_copy(update={'source':source+'a'}),proof)
