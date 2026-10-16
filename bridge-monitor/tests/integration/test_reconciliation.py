import uuid

import psycopg

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, mark_execution_uncertain, register_incarnation
from bridge.runtime.reconciliation import reconcile_execution
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup


def test_unproven_clearance_rejected_but_correlated_termination_releases_capacity(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-test-secret")
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    call = f"call-{uuid.uuid4()}"
    admit_call(dsn, _call(f"root-{call}", call), policy,
               policy_version=version, pool="claude", reserve_tokens=100)
    owner = f"owner-{uuid.uuid4()}"
    register_incarnation(dsn, owner, "worker-1")
    lease = claim_dispatch(dsn, pool="claude", incarnation_id=owner)
    mark_execution_uncertain(dsn, call, reason="heartbeat_expired")
    uncertain = reconcile_execution(dsn, call, {"kind": "resident_model_list", "models": []},
                                    actor_kind="operator", actor_id="operator-1", auth_token="operator-test-secret")
    assert uncertain == "uncertain"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT slot_held FROM runtime_jobs WHERE call_id=%s", (call,)).fetchone()[0]
    terminated = reconcile_execution(dsn, call,
        {"kind": "adapter_termination_receipt", "call_id": call, "lease_id": lease.lease_id,
         "owner_incarnation": owner, "terminated": True},
        actor_kind="operator", actor_id="operator-1", auth_token="operator-test-secret")
    assert terminated == "terminated"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT slot_held, state FROM runtime_jobs WHERE call_id=%s",
                            (call,)).fetchone() == (False, "terminated")


def test_heartbeat_or_operator_assertion_never_proves_termination(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-test-secret")
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    call = f"call-{uuid.uuid4()}"
    admit_call(dsn, _call(f"root-{call}", call), policy,
               policy_version=version, pool="claude", reserve_tokens=100)
    owner = f"owner-{uuid.uuid4()}"
    register_incarnation(dsn, owner, "worker-1")
    claim_dispatch(dsn, pool="claude", incarnation_id=owner)
    for evidence in ({"kind": "heartbeat_expired"}, {"kind": "operator_assertion", "terminated": True}):
        assert reconcile_execution(dsn, call, evidence, actor_kind="operator", actor_id="operator-1",
                                   auth_token="operator-test-secret") == "uncertain"
