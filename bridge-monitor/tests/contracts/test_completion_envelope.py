import pytest
from pydantic import ValidationError

from contracts.outcomes import CompletionEnvelope, DispositionRecord


def test_completion_envelope_preserves_answer_separate_from_accounting():
    envelope = CompletionEnvelope(call_id="claude-1", attempt_id="attempt-1",
        result={"result_id": "r-claude", "content_id": "c-claude", "delivered": True},
        dispositions=[], accounting_complete=False)
    assert envelope.result.delivered and not envelope.accounting_complete


def test_revised_cannot_claim_complete_replacement_effort():
    with pytest.raises(ValidationError, match="complete replacement"):
        DispositionRecord(disposition_id="d1", result_id="local-1", actor_id="worker-1",
                          stage="claude", judgment="revised", repair_effort="complete_replacement")


def test_part_level_link_and_hash_are_explicit():
    envelope = CompletionEnvelope(call_id="claude-1", attempt_id="attempt-1",
        result={"result_id": "r-claude", "content_id": "c-claude", "delivered": True},
        dispositions=[{"disposition_id": "d1", "result_id": "local-1", "actor_id": "worker-1",
                       "stage": "claude", "judgment": "revised", "part_id": "part-1",
                       "input_sha256": "a" * 64, "output_result_id": "r-claude"}],
        contributions=[{"from_result_id": "local-1", "to_result_id": "r-claude",
                        "relation": "revised_contribution"}])
    assert envelope.dispositions[0].part_id == "part-1"
