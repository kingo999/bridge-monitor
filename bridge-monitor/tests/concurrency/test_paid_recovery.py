import uuid

import psycopg
import pytest

from bridge.runtime.reconciliation import reconcile_execution
from bridge.runtime.recovery import schedule_paid_recovery, reconcile_owner_and_recover
from bridge.runtime.scheduler import cancel_queued, request_cancel_tree
from bridge.runtime.ownership import claim_dispatch
from tests.integration.test_dispositions import _seed_chain
from tests.integration.test_admission import _call, _policy
from bridge.runtime.admission import admit_call


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
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("fallback", True)


def test_rejected_recovery_still_retires_orphaned_queued_child(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False, budget=2,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
        version = conn.execute("SELECT version FROM runtime_active_policy WHERE singleton=true").fetchone()[0]
    reconcile_execution(dsn, owner, {"kind": "process_exit_with_call_correlation",
        "call_id": owner, "lease_id": lease, "owner_incarnation": incarnation,
        "terminated": True}, actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test")
    blocker = f"blocker-{uuid.uuid4()}"
    assert admit_call(dsn, _call(f"root-{blocker}", blocker), _policy(),
        policy_version=version, pool="claude", reserve_tokens=100).state == "queued"
    recovery = f"recovery-{uuid.uuid4()}"
    rejected = schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
        auth_token="system-recovery-test", reason="owner_terminated")
    assert rejected.state == "rejected" and rejected.reason == "queue_full"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("cancelled", False)
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=f"inc-{child}") is None
    assert cancel_queued(dsn, blocker)
    assert schedule_paid_recovery(dsn, child, recovery, actor_id="coordinator",
        auth_token="system-recovery-test", reason="owner_terminated").state == "queued"


def test_queued_local_child_cannot_dispatch_after_paid_recovery_is_selected(
        runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False, budget=2,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "process_exit_with_call_correlation",
        "call_id": owner, "lease_id": lease, "owner_incarnation": incarnation,
        "terminated": True}, actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test")
    decision = schedule_paid_recovery(dsn, child, f"recovery-{uuid.uuid4()}",
        actor_id="coordinator", auth_token="system-recovery-test",
        reason="owner_terminated")
    assert decision.state == "queued"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("cancelled", False)
        assert conn.execute("SELECT calls_reserved FROM runtime_task_budgets WHERE task_id="
                            "(SELECT task_id FROM runtime_jobs WHERE call_id=%s)",
                            (child,)).fetchone()[0] == 2
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=f"inc-{child}") is None


def test_running_local_child_is_late_only_after_paid_recovery_selection(
        runtime_dsn, monkeypatch):
    from tests.integration.test_dispositions import _local_result

    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, bindings = _seed_chain(dsn, budget=3,
                                          recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "process_exit_with_call_correlation",
        "call_id": owner, "lease_id": lease, "owner_incarnation": incarnation,
        "terminated": True}, actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test")
    decision = schedule_paid_recovery(dsn, child, f"recovery-{uuid.uuid4()}",
        actor_id="coordinator", auth_token="system-recovery-test",
        reason="owner_terminated")
    assert decision.state == "queued"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("fallback", True)
    receipt = _local_result(dsn, child, bindings[child])
    assert not receipt.answer.delivered
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT delivered_at FROM results WHERE call_id=%s",
                            (child,)).fetchone()[0] is None
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("completed", False)


def test_orphaned_child_remains_late_only_after_uncertain_reconciliation(
        runtime_dsn, monkeypatch):
    from tests.integration.test_dispositions import _local_result

    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, bindings = _seed_chain(dsn, budget=3,
                                          recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        owner_lease, owner_inc = conn.execute(
            "SELECT lease_id,owner_incarnation FROM runtime_jobs WHERE call_id=%s",
            (owner,)).fetchone()
    evidence = {"kind": "process_exit_with_call_correlation", "call_id": owner,
                "lease_id": owner_lease, "owner_incarnation": owner_inc,
                "terminated": True}
    assert reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="system-recovery-test")["recoveries"][0]["state"] == "queued"
    assert reconcile_execution(dsn, child, {"kind": "heartbeat_expired"},
        actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test") == "uncertain"
    receipt = _local_result(dsn, child, bindings[child])
    assert not receipt.answer.delivered
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("completed", False)
        assert conn.execute("SELECT count(*) FROM runtime_disposition_obligations "
                            "WHERE result_id=%s", (f"result-{child}",)).fetchone()[0] == 0


def test_repeated_owner_reconciliation_reports_same_queued_child_recovery(
        runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, dispatch_qwen=False, budget=2,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    evidence = {"kind": "process_exit_with_call_correlation", "call_id": owner,
                "lease_id": lease, "owner_incarnation": incarnation, "terminated": True}
    first = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="system-recovery-test")
    second = reconcile_owner_and_recover(dsn, owner, evidence, actor_kind="system",
        actor_id="coordinator", auth_token="system-recovery-test")
    assert first == second
    assert first["recoveries"][0]["child_call_id"] == child


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


def test_owner_reconciliation_rolls_back_if_child_recovery_fails(runtime_dsn, monkeypatch):
    from bridge.runtime import recovery as module

    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=3,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        before = conn.execute("SELECT state,lease_id,owner_incarnation FROM runtime_jobs "
                              "WHERE call_id=%s", (owner,)).fetchone()
    def fail_after_reconciliation(*args, **kwargs):
        raise RuntimeError("simulated crash before child recovery commits")
    monkeypatch.setattr(module, "_schedule_paid_recovery_in_tx", fail_after_reconciliation)
    with pytest.raises(RuntimeError, match="simulated crash"):
        reconcile_owner_and_recover(dsn, owner, {
            "kind": "process_exit_with_call_correlation", "call_id": owner,
            "lease_id": before[1], "owner_incarnation": before[2], "terminated": True},
            actor_kind="system", actor_id="coordinator",
            auth_token="system-recovery-test")
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                            (owner,)).fetchone()[0] == before[0]
        assert conn.execute("SELECT count(*) FROM runtime_reconciliations WHERE call_id=%s",
                            (owner,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM runtime_recovery_links WHERE child_call_id=%s",
                            (child,)).fetchone()[0] == 0


def test_owner_recovery_rejection_is_durable_in_reconciliation(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=2,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    outcome = reconcile_owner_and_recover(dsn, owner, {
        "kind": "process_exit_with_call_correlation", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test")
    assert outcome["recoveries"][0]["state"] == "rejected"
    assert outcome["recoveries"][0]["reason"] == "root_budget_exhausted"
    with psycopg.connect(dsn) as conn:
        stored = conn.execute("SELECT evidence->'recovery_outcomes' FROM runtime_reconciliations "
                              "WHERE call_id=%s ORDER BY recorded_at DESC LIMIT 1",
                              (owner,)).fetchone()[0]
        assert stored == outcome["recoveries"]
        assert stored[0]["child_call_id"] == child
    from service.queries.history import read_work_item_detail
    with psycopg.connect(dsn) as conn:
        work_id = conn.execute("SELECT work_item_id FROM runtime_jobs WHERE call_id=%s",
                               (owner,)).fetchone()[0]
    detail = read_work_item_detail(dsn, work_id)
    assert detail["recovery_decisions"][-1]["outcomes"] == outcome["recoveries"]


def test_completed_local_child_still_needs_paid_recovery_when_owner_dies(
        runtime_dsn, monkeypatch):
    from tests.integration.test_dispositions import _local_result

    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, bindings = _seed_chain(dsn, budget=3,
                                          recipient_call_id=f"gpt-{uuid.uuid4()}")
    assert _local_result(dsn, child, bindings[child]).answer.delivered
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    outcome = reconcile_owner_and_recover(dsn, owner, {
        "kind": "process_exit_with_call_correlation", "call_id": owner,
        "lease_id": lease, "owner_incarnation": incarnation, "terminated": True},
        actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test")
    assert outcome["recoveries"][0]["state"] == "queued"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT state FROM runtime_jobs WHERE call_id=%s",
                            (child,)).fetchone() == ("completed",)


def test_expired_recovery_prompt_rejects_before_queuing_paid_work(runtime_dsn, monkeypatch):
    dsn = runtime_dsn
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "system-recovery-test")
    owner, child, _ = _seed_chain(dsn, budget=3,
                                  recipient_call_id=f"gpt-{uuid.uuid4()}")
    with psycopg.connect(dsn) as conn:
        lease, incarnation = conn.execute("SELECT lease_id,owner_incarnation FROM runtime_jobs "
                                          "WHERE call_id=%s", (owner,)).fetchone()
    reconcile_execution(dsn, owner, {"kind": "adapter_termination_receipt",
        "call_id": owner, "lease_id": lease, "owner_incarnation": incarnation,
        "terminated": True}, actor_kind="system", actor_id="coordinator",
        auth_token="system-recovery-test")
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE content_objects SET raw_bytes=NULL,completeness='removed' "
                     "WHERE content_id=%s", (f"prompt-{child}",))
    recovery_id = f"recovery-{uuid.uuid4()}"
    decision = schedule_paid_recovery(dsn, child, recovery_id,
        actor_id="coordinator", auth_token="system-recovery-test",
        reason="owner_terminated")
    assert decision.state == "rejected"
    assert decision.reason == "recovery_prompt_unavailable"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE call_id=%s",
                            (recovery_id,)).fetchone()[0] == 0
