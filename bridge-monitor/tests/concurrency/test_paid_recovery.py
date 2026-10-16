import uuid

import psycopg
import pytest

from bridge.runtime.reconciliation import reconcile_execution
from bridge.runtime.recovery import schedule_paid_recovery, reconcile_owner_and_recover
from bridge.runtime.scheduler import request_cancel_tree
from tests.integration.test_dispositions import _seed_chain


def test_recovery_requires_confirmed_owner_termination_and_preserves_child_lineage(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    recipient = f"gpt-{uuid.uuid4()}"
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=recipient)
    recovery = f"recovery-{uuid.uuid4()}"
    with pytest.raises(ValueError, match="confirmed owner termination"):
        schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
                               auth_token="system-recovery-test", reason="owner_lost")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    assert reconcile_execution(dsn, owner, {
        "kind": "process_exit_with_call_correlation", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator", auth_token="system-recovery-test") == "terminated"
    decision = schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
                                      auth_token="system-recovery-test", reason="owner_lost")
    assert decision.state == "queued" and decision.route == "paid"
    repeated = schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
                                      auth_token="system-recovery-test", reason="owner_lost")
    assert repeated == decision
    with psycopg.connect(dsn) as conn:
        link = conn.execute("SELECT child_call_id,recipient_call_id,reason FROM runtime_recovery_links "
                            "WHERE recovery_call_id=%s", (recovery,)).fetchone()
        call = conn.execute("SELECT parent_call_id,work_item_id FROM calls WHERE call_id=%s",
                            (recovery,)).fetchone()
        child_work = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                                  (child,)).fetchone()[0]
        assert link == (child, recipient, "owner_lost")
        assert call == (recipient, child_work)
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child,)).fetchone()[0] == 1
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (owner,)).fetchone() == ("terminated", False)


def test_recovery_never_exceeds_root_paid_call_budget(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=2, recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "adapter_termination_receipt", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator", auth_token="system-recovery-test")
    decision = schedule_paid_recovery(dsn, child, f"recovery-{uuid.uuid4()}",
        actor_id="coordinator", auth_token="system-recovery-test", reason="owner_lost")
    assert decision.state == "rejected" and decision.reason == "root_budget_exhausted"


def test_parent_cancellation_reaches_queued_recovery_and_active_local_child(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    recipient = f"gpt-{uuid.uuid4()}"
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=recipient)
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "adapter_termination_receipt", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator", auth_token="system-recovery-test")
    recovery = f"recovery-{uuid.uuid4()}"
    assert schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
        auth_token="system-recovery-test", reason="owner_lost").state == "queued"
    affected = request_cancel_tree(dsn, recipient, reason="parent_cancelled")
    assert recovery in affected and child in affected
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (recovery,)).fetchone() == ("cancelled", False)
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("uncertain", True)
        assert conn.execute("SELECT calls_reserved FROM runtime_task_budgets WHERE task_id="
                            "(SELECT task_id FROM runtime_jobs WHERE call_id=%s)",
                            (child,)).fetchone()[0] == 2


def test_cancelling_owned_claude_call_reaches_sibling_recovery(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "adapter_termination_receipt", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator", auth_token="system-recovery-test")
    recovery = f"recovery-{uuid.uuid4()}"
    assert schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
        auth_token="system-recovery-test", reason="owner_lost").state == "queued"
    assert recovery in request_cancel_tree(dsn, owner, reason="owner_cancelled")
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                            (recovery,)).fetchone() == ("cancelled",)


def test_cancelled_terminated_owner_cannot_queue_late_recovery(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "adapter_termination_receipt", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator", auth_token="system-recovery-test")
    request_cancel_tree(dsn, owner, reason="parent_cancelled")
    outcome = schedule_paid_recovery(dsn, child, f"recovery-{uuid.uuid4()}",
        actor_id="coordinator", auth_token="system-recovery-test", reason="owner_terminated")
    assert outcome.state == "rejected" and outcome.reason == "parent_cancelled"


def test_confirmed_owner_reconciliation_schedules_one_recovery(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    evidence = {"kind": "process_exit_with_call_correlation", "call_id": owner,
                "lease_id": lease, "owner_incarnation": incarnation, "terminated": True}
    first = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="operator",
                                         actor_id="operator", auth_token="operator-recovery-test")
    second = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="operator",
                                          actor_id="operator", auth_token="operator-recovery-test")
    assert first["state"] == second["state"] == "terminated"
    assert first["recoveries"] == second["recoveries"]
    assert first["recoveries"][0]["child_call_id"] == child
    assert first["recoveries"][0]["state"] == "queued"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child,)).fetchone()[0] == 1


def test_uncertain_owner_does_not_schedule_recovery(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=3, recipient_call_id=f"gpt-{uuid.uuid4()}")
    outcome = reconcile_owner_and_recover(dsn, owner, {"kind": "heartbeat_expired"},
        actor_kind="operator", actor_id="operator", auth_token="operator-recovery-test")
    assert outcome == {"state": "uncertain", "recoveries": []}
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child,)).fetchone()[0] == 0
