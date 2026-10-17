"""Fallback and reconciliation race invariants on durable test state."""

import uuid
from datetime import datetime, timezone

import psycopg

from bridge.runtime.completions import record_completion
from bridge.runtime.fallback import commit_fallback
from bridge.runtime.reconciliation import reconcile_execution
from bridge.runtime.scheduler import request_cancel_tree
from contracts.outcomes import CompletionEnvelope
from tests.integration.test_dispositions import _seed_chain


def test_confirmed_live_child_cannot_undo_selected_fallback(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "race-review-token")
    dsn = runtime_dsn
    owner, child, bindings = _seed_chain(dsn)
    transition = commit_fallback(dsn, child, timeout_stage="execution",
        reason="local_cutoff", threshold_at=datetime.now(timezone.utc), elapsed_ms=1000,
        recipient_call_id=owner, owner_execution_id=f"{owner}:model:1",
        selected_model="claude-test")
    assert transition.state == "committed"
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation "
            "FROM runtime_jobs WHERE call_id=%s", (child,)).fetchone()
    assert reconcile_execution(dsn, child, {"kind": "adapter_live_receipt",
        "call_id": child, "lease_id": lease, "owner_incarnation": incarnation,
        "live": True}, actor_kind="operator", actor_id="reviewer",
        auth_token="race-review-token") == "live"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("fallback", True)
    result_id = f"late-{uuid.uuid4()}"
    receipt = record_completion(dsn, CompletionEnvelope.model_validate({
        "call_id": child, "attempt_id": bindings[child].attempt_id,
        "result": {"result_id": result_id, "content_id": f"content-{result_id}",
                   "delivered": True}}), bindings[child],
        answer_bytes=b"late local result", consumer_stage="claude")
    assert not receipt.answer.delivered
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("completed", False)
        assert conn.execute("SELECT count(*) FROM runtime_disposition_obligations "
            "WHERE result_id=%s", (result_id,)).fetchone()[0] == 0


def test_cancellation_during_fallback_cannot_restore_local_delivery(runtime_dsn):
    dsn = runtime_dsn
    owner, child, bindings = _seed_chain(dsn)
    commit_fallback(dsn, child, timeout_stage="execution", reason="local_cutoff",
        threshold_at=datetime.now(timezone.utc), elapsed_ms=1000,
        recipient_call_id=owner, owner_execution_id=f"{owner}:model:1",
        selected_model="claude-test")
    assert child in request_cancel_tree(dsn, child, reason="caller_cancelled")
    receipt = record_completion(dsn, CompletionEnvelope.model_validate({
        "call_id": child, "attempt_id": bindings[child].attempt_id,
        "result": {"result_id": f"late-{child}", "content_id": f"late-content-{child}",
                   "delivered": True}}), bindings[child],
        answer_bytes=b"late after cancellation", consumer_stage="claude")
    assert not receipt.answer.delivered
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("completed", False)
