"""Larger requests fit without truncation; input plus reserved output stays bounded."""
import unittest

from bridge.local_worker.models import TaskPacket, WorkerError
from bridge.local_worker.prompts import PromptBudgeter, baseline_proof
from tests.local_worker_helpers import packet_data


class ExpandedBudget(unittest.TestCase):
    def test_request_larger_than_old_byte_budget_is_preserved(self):
        packet = TaskPacket(**(packet_data() | {'source': 'x' * 9000}))
        prepared = PromptBudgeter().prepare(packet, baseline_proof())
        self.assertIn(packet.source, prepared.text)
        self.assertGreater(prepared.upper_bound, 3072)
        self.assertLessEqual(prepared.upper_bound, 14336)

    def test_new_boundary_reserves_2048_output_in_16384_context(self):
        packet = TaskPacket(**packet_data())
        proof = baseline_proof()
        current = PromptBudgeter().prepare(packet, proof).upper_bound
        at_limit = packet.model_copy(update={'source': packet.source + 'a' * (14336 - current)})
        self.assertEqual(PromptBudgeter().prepare(at_limit, proof).upper_bound, 14336)
        with self.assertRaises(WorkerError):
            PromptBudgeter().prepare(at_limit.model_copy(update={'source': at_limit.source + 'a'}), proof)
