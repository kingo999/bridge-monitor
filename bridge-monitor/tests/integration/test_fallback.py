import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from bridge.runtime.fallback import commit_fallback
from bridge.runtime.completions import record_completion
from contracts.outcomes import CompletionEnvelope
from tests.integration.test_dispositions import _seed_chain


def _fallback(dsn, claude, qwen, *, stage="execution"):
    return commit_fallback(dsn, qwen, timeout_stage=stage, reason="deadline_exceeded",
        threshold_at=datetime.now(timezone.utc), elapsed_ms=1000,
        recipient_call_id=claude, owner_execution_id=f"{claude}:model:1",
        selected_model="claude-test")


def test_postdispatch_fallback_is_idempotent_and_keeps_local_slot(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    first = _fallback(dsn, claude, qwen)
    second = _fallback(dsn, claude, qwen)
    assert first == second and first.state == "committed"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (qwen,)).fetchone() == ("fallback", True)
        decisions = conn.execute("SELECT route,reason FROM delegation_decisions WHERE child_call_id=%s",
                                 (qwen,)).fetchall()
        assert len(decisions) == 1 and decisions[0][0] == "paid"


def test_late_result_after_fallback_is_retained_undelivered(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    _fallback(dsn, claude, qwen)
    result = record_completion(dsn, CompletionEnvelope.model_validate({
        "call_id": qwen, "attempt_id": bindings[qwen].attempt_id,
        "result": {"result_id": f"late-{qwen}", "content_id": f"late-content-{qwen}",
                   "delivered": True}}), bindings[qwen], answer_bytes=b"late local output",
        consumer_stage="claude")
    assert not result.answer.delivered
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT delivered_at FROM results WHERE result_id=%s",
                            (f"late-{qwen}",)).fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM runtime_disposition_obligations WHERE result_id=%s",
                            (f"late-{qwen}",)).fetchone()[0] == 0


def test_queue_expiry_cannot_replace_a_dispatch_winner(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, _ = _seed_chain(dsn)
    with pytest.raises(ValueError, match="dispatch won"):
        _fallback(dsn, claude, qwen, stage="queue")


def test_predispatch_paid_route_is_not_routed_and_forbids_later_dispatch(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, _ = _seed_chain(dsn, dispatch_qwen=False)
    transition = _fallback(dsn, claude, qwen, stage="queue")
    assert transition.state == "committed"
    from bridge.runtime.ownership import claim_dispatch
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=f"inc-{qwen}", call_id=qwen) is None
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (qwen,)).fetchone() == ("fallback", False)
        assert conn.execute("SELECT route,gate_facts->>'pre_dispatch' FROM delegation_decisions "
                            "WHERE child_call_id=%s", (qwen,)).fetchone() == ("paid", "true")


def test_completed_local_result_blocks_later_fallback(runtime_dsn):
    dsn = runtime_dsn
    claude, qwen, bindings = _seed_chain(dsn)
    record_completion(dsn, CompletionEnvelope.model_validate({
        "call_id": qwen, "attempt_id": bindings[qwen].attempt_id,
        "result": {"result_id": f"result-{qwen}", "content_id": f"content-{qwen}",
                   "delivered": True}}), bindings[qwen], answer_bytes=b"local answer",
        consumer_stage="claude")
    with pytest.raises(ValueError, match="completion"):
        _fallback(dsn, claude, qwen)
