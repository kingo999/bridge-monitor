import uuid
from datetime import datetime, timezone, timedelta

import psycopg

from contracts.policy import PolicyInput
from contracts.work import CallRequest, TaskPacket
from database.migrate import migrate
from bridge.runtime.admission import admit_call


def _policy():
    return PolicyInput.model_validate({
        "limits": {"claude_global_active": 1, "claude_per_root_active": 1,
                   "claude_waiting": 1, "qwen_waiting": 1},
        "deployment": {"queue_deadline_seconds": 30, "context_wait_seconds": 10,
                       "fallback_allowance_tokens": 100, "client_timeout_seconds": 90,
                       "delivery_margin_seconds": 5, "payload_limit_bytes": 100000,
                       "spool_limit_bytes": 100000, "retention_days": 30,
                       "postgres_dsn_ref": "env:BRIDGE_MONITOR_DSN",
                       "qwen_endpoint": "http://127.0.0.1:8000"},
        "fallback": {"paid_model_profile": "claude-test"},
        "review": {"reviewer": "human", "approval_reference": "review-1"},
    })


def _call(task_id, call_id, budget=2):
    return CallRequest(call_id=call_id, task=TaskPacket(task_id=task_id,
        work_item_id=f"work-{call_id}", category="analysis", scope=["read"],
        token_budget=1000, call_budget=budget), requested_model="claude-test")


def _admit_local(test_dsn, request, policy, reserve_tokens):
    return admit_call(test_dsn, request, policy, policy_version="test-v1", pool="qwen",
                      reserve_tokens=reserve_tokens,
                      local_cutoff_at=datetime.now(timezone.utc) + timedelta(seconds=30))


def test_queue_limit_and_root_budget_are_durable(runtime_dsn):
    test_dsn = runtime_dsn
    policy = _policy()
    root = f"root-{uuid.uuid4()}"
    first = admit_call(test_dsn, _call(root, f"call-{uuid.uuid4()}"), policy,
                       policy_version="test-v1", pool="claude", reserve_tokens=100)
    second = admit_call(test_dsn, _call(root, f"call-{uuid.uuid4()}"), policy,
                        policy_version="test-v1", pool="claude", reserve_tokens=100)
    assert first.state == "queued"
    assert second.state == "rejected" and second.reason == "queue_full"
    with psycopg.connect(test_dsn) as conn:
        row = conn.execute("SELECT calls_reserved, tokens_reserved FROM runtime_task_budgets WHERE task_id=%s", (root,)).fetchone()
    assert row == (1, 100)


def test_duplicate_admission_is_idempotent_and_conflicting_pin_rejected(runtime_dsn):
    test_dsn = runtime_dsn
    policy = _policy()
    root, call = f"root-{uuid.uuid4()}", f"call-{uuid.uuid4()}"
    request = _call(root, call)
    one = _admit_local(test_dsn, request, policy, 50)
    two = _admit_local(test_dsn, request, policy, 50)
    assert one == two
    conflicting = request.model_copy(update={"task": request.task.model_copy(update={"scope": ["write"]})})
    three = _admit_local(test_dsn, conflicting, policy, 50)
    assert three.state == "rejected" and three.reason == "task_pin_conflict"


def test_root_call_and_token_budget_reject_without_reservation(runtime_dsn):
    test_dsn = runtime_dsn
    policy = _policy().model_copy(update={"limits": _policy().limits.model_copy(update={"qwen_waiting": 3})})
    root = f"root-{uuid.uuid4()}"
    one = _admit_local(test_dsn, _call(root, f"call-{uuid.uuid4()}", budget=1), policy, 900)
    two = _admit_local(test_dsn, _call(root, f"call-{uuid.uuid4()}", budget=1), policy, 1)
    assert one.state == "queued"
    assert two.state == "rejected" and two.reason == "root_budget_exhausted"
