import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from bridge.runtime.completions import record_completion
from bridge.runtime.fallback import commit_fallback
from contracts.events import EventEnvelope, EventType
from contracts.outcomes import CompletionEnvelope
from service.ingest.store import ingest_batch
from tests.integration.test_dispositions import _seed_chain


def test_in_owner_paid_continuation_uses_same_execution_and_keeps_uncertain_local_slot(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    commit_fallback(dsn, qwen, timeout_stage="execution", reason="deadline_exceeded",
        threshold_at=datetime.now(timezone.utc), elapsed_ms=1000,
        recipient_call_id=claude, owner_execution_id=f"{claude}:model:1",
        selected_model="claude-test")
    envelope = CompletionEnvelope.model_validate({"call_id": claude,
        "attempt_id": bindings[claude].attempt_id,
        "result": {"result_id": f"paid-{claude}", "content_id": f"paid-content-{claude}",
                   "delivered": True}, "fallback_child_call_id": qwen})
    receipt = record_completion(dsn, envelope, bindings[claude], answer_bytes=b"paid continuation")
    assert receipt.answer.delivered and receipt.accounting_complete
    producer_id = f"usage-{uuid.uuid4()}"
    event = EventEnvelope(event_id=str(uuid.uuid4()), event_type=EventType.USAGE_REPORTED,
        schema_version=1, producer_id=producer_id, producer_incarnation="boot-1",
        producer_sequence=1, occurred_at=datetime.now(timezone.utc), origin="observed",
        observation_layer="bridge", capture_completeness="complete", call_id=claude,
        attempt_id=bindings[claude].attempt_id, model_execution_id=f"{claude}:model:1",
        payload={"observation_id": str(uuid.uuid4()), "scope": "execution",
                 "is_cumulative": True, "input_tokens": 120, "output_tokens": 33,
                 "provenance": "claude_cli_result"})
    assert ingest_batch(dsn, producer_id, [event], []) == ["committed"]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE task_id=(SELECT task_id "
                            "FROM runtime_jobs WHERE call_id=%s) AND pool='claude'", (claude,)).fetchone()[0] == 1
        assert conn.execute("SELECT state,paid_result_id FROM runtime_fallback_transitions "
                            "WHERE child_call_id=%s", (qwen,)).fetchone() == ("completed", f"paid-{claude}")
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (qwen,)).fetchone() == ("fallback", True)
        assert conn.execute("SELECT input_tokens,output_tokens FROM usage_observations "
                            "WHERE model_execution_id=%s", (f"{claude}:model:1",)).fetchone() == (120, 33)
        assert conn.execute("SELECT count(*) FROM work_resolutions WHERE work_item_id=(SELECT work_item_id "
                            "FROM runtime_jobs WHERE call_id=%s)", (claude,)).fetchone()[0] == 0


@pytest.mark.parametrize("stage,resolution,expected", [
    ("execution", "resolved", "paid_replaced"),
    ("execution", "unresolved", None),
    ("queue", "resolved", "paid_direct")])
def test_takeover_requires_dispatched_local_and_resolved_paid_answer(runtime_dsn,
                                                                     stage,resolution,expected):
    claude, qwen, bindings = _seed_chain(runtime_dsn,
        dispatch_qwen=stage != "queue")
    commit_fallback(runtime_dsn, qwen, timeout_stage=stage, reason="deadline_exceeded",
        threshold_at=datetime.now(timezone.utc), elapsed_ms=1000,
        recipient_call_id=claude, owner_execution_id=f"{claude}:model:1",
        selected_model="claude-test")
    envelope = CompletionEnvelope.model_validate({"call_id": claude,
        "attempt_id": bindings[claude].attempt_id,
        "result": {"result_id": f"paid-{claude}", "content_id": f"content-{claude}",
                   "delivered": True},
        "fallback_child_call_id": qwen, "work_resolution": resolution})
    record_completion(runtime_dsn, envelope, bindings[claude], answer_bytes=b"paid answer")
    with psycopg.connect(runtime_dsn) as conn:
        rows = conn.execute("SELECT outcome FROM work_resolutions WHERE work_item_id=(SELECT "
            "work_item_id FROM runtime_jobs WHERE call_id=%s)", (claude,)).fetchall()
    assert rows == ([(expected,)] if expected else [])
