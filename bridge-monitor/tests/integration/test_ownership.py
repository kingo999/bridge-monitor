import uuid
from concurrent.futures import ProcessPoolExecutor

import psycopg

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import (claim_dispatch, register_incarnation,
                                      mark_execution_uncertain, claim_launch)
from bridge.runtime.policy_startup import activate_policy
from tests.integration.test_admission import _call, _policy


def _claim_in_process(dsn, incarnation):
    return claim_dispatch(dsn, pool="claude", incarnation_id=incarnation)


def _setup(dsn):
    policy = _policy()
    proposed = policy.model_dump()
    from contracts.policy import validate_policy
    digest = validate_policy(proposed, {"claude-test": {"context_tokens": 10000}}).snapshot.source_hash
    active = activate_policy(dsn, proposed, {"claude-test": {"context_tokens": 10000}},
                             approved_hash=digest, operator_id="operator-1")
    return policy, active.version


def test_two_processes_claim_one_slot_and_lost_ack_is_idempotent(runtime_dsn):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    policy = policy.model_copy(update={"limits": policy.limits.model_copy(update={"claude_waiting": 2})})
    calls = [f"call-{uuid.uuid4()}" for _ in range(2)]
    for call in calls:
        decision = admit_call(dsn, _call(f"root-{call}", call), policy,
                              policy_version=version, pool="claude", reserve_tokens=100)
        assert decision.state == "queued"
    owners = [f"owner-{uuid.uuid4()}" for _ in range(2)]
    for owner in owners:
        register_incarnation(dsn, owner, f"worker-{owner}")
    with ProcessPoolExecutor(max_workers=2) as workers:
        leases = list(workers.map(_claim_in_process, [dsn, dsn], owners))
    assert sum(lease is not None for lease in leases) == 1
    winner = next((owner, lease) for owner, lease in zip(owners, leases) if lease)
    repeated = claim_dispatch(dsn, pool="claude", incarnation_id=winner[0], call_id=winner[1].call_id)
    assert repeated == winner[1]
    assert claim_launch(dsn, winner[1])
    assert not claim_launch(dsn, winner[1])
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE slot_held").fetchone()[0] == 1


def test_uncertain_owner_keeps_slot_and_fences_restart(runtime_dsn):
    dsn = runtime_dsn
    policy, version = _setup(dsn)
    policy = policy.model_copy(update={"limits": policy.limits.model_copy(update={"claude_waiting": 2})})
    first, second = f"call-{uuid.uuid4()}", f"call-{uuid.uuid4()}"
    for call in (first, second):
        admit_call(dsn, _call(f"root-{call}", call), policy,
                   policy_version=version, pool="claude", reserve_tokens=100)
    old, new = f"owner-{uuid.uuid4()}", f"owner-{uuid.uuid4()}"
    register_incarnation(dsn, old, "worker-1")
    register_incarnation(dsn, new, "worker-1")
    lease = claim_dispatch(dsn, pool="claude", incarnation_id=old)
    mark_execution_uncertain(dsn, lease.call_id, reason="heartbeat_expired")
    assert claim_dispatch(dsn, pool="claude", incarnation_id=new) is None
    import pytest
    with pytest.raises(ValueError, match="live registered"):
        claim_dispatch(dsn, pool="claude", incarnation_id=old, call_id=lease.call_id)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT slot_held, state FROM runtime_jobs WHERE call_id=%s",
                            (lease.call_id,)).fetchone() == (True, "uncertain")
