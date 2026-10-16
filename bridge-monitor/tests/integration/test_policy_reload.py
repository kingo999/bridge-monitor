import uuid

import psycopg

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from bridge.runtime.policy_reload import reload_policy
from bridge.runtime.policy_startup import active_policy
from bridge.runtime.reconciliation import reconcile_execution
from contracts.policy import validate_policy
from tests.concurrency.test_limits import _activate_two
from tests.integration.test_admission import _call


CAPABILITIES = {"claude-test": {"context_tokens": 10000}}


def _reload(dsn, proposed, *, hash_override=None):
    validated = validate_policy(proposed, CAPABILITIES)
    approved_hash = (hash_override if hash_override is not None else
                     validated.snapshot.source_hash if validated.snapshot else "invalid")
    return reload_policy(dsn, proposed, CAPABILITIES, approved_hash=approved_hash,
                         operator_id="operator-1", auth_token="operator-secret")


def test_rejected_and_restart_only_changes_preserve_last_known_good(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-secret")
    dsn = runtime_dsn
    _, version = _activate_two(dsn)
    base = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    malformed = {**base, "unknown_setting": True}
    assert _reload(dsn, malformed).status == "rejected"
    missing_review = {**base, "review": {**base["review"], "approval_reference": "REQUIRED"}}
    assert _reload(dsn, missing_review).status == "rejected"
    changed_endpoint = {**base, "deployment": {**base["deployment"],
                         "qwen_endpoint": "http://127.0.0.1:12345"}}
    assert _reload(dsn, changed_endpoint).status == "restart_required"
    assert active_policy(dsn).version == version
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_policy_activations").fetchone()[0] == 1


def test_capacity_reduction_keeps_two_active_and_blocks_third_until_both_release(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-secret")
    dsn = runtime_dsn
    policy, version = _activate_two(dsn)
    calls = [f"call-{uuid.uuid4()}" for _ in range(3)]
    owners = [f"owner-{uuid.uuid4()}" for _ in range(3)]
    for call, owner in zip(calls, owners):
        assert admit_call(dsn, _call(f"root-{call}", call), policy,
                          policy_version=version, pool="claude", reserve_tokens=100).state == "queued"
        register_incarnation(dsn, owner, f"worker-{owner}")
    leases = [claim_dispatch(dsn, pool="claude", incarnation_id=owner) for owner in owners[:2]]
    assert all(leases)
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["limits"].update(claude_global_active=1, claude_per_root_active=1,
                              claude_waiting=1)
    outcome = _reload(dsn, proposed)
    assert outcome.status == "activated" and outcome.version != version
    assert claim_dispatch(dsn, pool="claude", incarnation_id=owners[2]) is None
    for index, lease in enumerate(leases):
        reconcile_execution(dsn, lease.call_id, {"kind": "adapter_termination_receipt",
            "call_id": lease.call_id, "lease_id": lease.lease_id,
            "owner_incarnation": owners[index], "terminated": True}, actor_kind="operator",
            actor_id="operator-1", auth_token="operator-secret")
        if index == 0:
            assert claim_dispatch(dsn, pool="claude", incarnation_id=owners[2]) is None
    remaining = set(calls) - {lease.call_id for lease in leases}
    assert claim_dispatch(dsn, pool="claude", incarnation_id=owners[2]).call_id in remaining
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE state='cancelled'").fetchone()[0] == 0


def test_unreviewed_hash_cannot_activate(runtime_dsn, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_OPERATOR_TOKEN", "operator-secret")
    dsn = runtime_dsn
    _, version = _activate_two(dsn)
    proposed = active_policy(dsn).model_dump(exclude={"version", "source_hash"})
    proposed["limits"]["claude_waiting"] = 3
    result = _reload(dsn, proposed, hash_override="wrong")
    assert result.status == "rejected" and active_policy(dsn).version == version
