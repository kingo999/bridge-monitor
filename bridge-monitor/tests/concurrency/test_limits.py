import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from bridge.runtime.policy_startup import activate_policy
from contracts.policy import validate_policy
from contracts.queries import CohortQuery
from service.queries.snapshot import read_snapshot
from tests.integration.test_admission import _call, _policy


def _activate_two(dsn):
    base = _policy()
    limits = base.limits.model_copy(update={"claude_global_active": 2,
                                            "claude_per_root_active": 2,
                                            "claude_waiting": 8,
                                            "qwen_active": 1, "qwen_waiting": 4})
    policy = base.model_copy(update={"limits": limits})
    capabilities = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(policy.model_dump(), capabilities).snapshot.source_hash
    snapshot = activate_policy(dsn, policy.model_dump(), capabilities,
                               approved_hash=digest, operator_id="operator-1")
    return policy, snapshot.version


def _claim(dsn, pool, owner):
    return claim_dispatch(dsn, pool=pool, incarnation_id=owner)


def test_two_processes_share_paid_limit_and_qwen_has_separate_capacity(runtime_dsn):
    dsn = runtime_dsn
    policy, version = _activate_two(dsn)
    roots = [f"root-{uuid.uuid4()}" for _ in range(3)]
    paid_calls = [f"call-{uuid.uuid4()}" for _ in roots]
    for root, call in zip(roots, paid_calls):
        assert admit_call(dsn, _call(root, call), policy, policy_version=version,
                          pool="claude", reserve_tokens=100).state == "queued"
    local_call = f"call-{uuid.uuid4()}"
    assert admit_call(dsn, _call(roots[0], local_call), policy, policy_version=version,
                      pool="qwen", reserve_tokens=100,
                      local_cutoff_at=datetime.now(timezone.utc) + timedelta(seconds=30)).state == "queued"
    owners = [f"owner-{uuid.uuid4()}" for _ in range(4)]
    for owner in owners:
        register_incarnation(dsn, owner, f"worker-{owner}")
    with ProcessPoolExecutor(max_workers=3) as workers:
        leases = list(workers.map(_claim, [dsn] * 3, ["claude"] * 3, owners[:3]))
    assert sum(lease is not None for lease in leases) == 2
    assert claim_dispatch(dsn, pool="qwen", incarnation_id=owners[3]) is not None
    with psycopg.connect(dsn) as conn:
        counts = conn.execute("SELECT pool,count(*) FROM runtime_jobs WHERE slot_held GROUP BY pool").fetchall()
        paid = conn.execute("SELECT call_id,work_item_id,worker_instance_id FROM runtime_jobs "
                            "WHERE pool='claude' AND state='active'").fetchall()
        for call_id, work_item_id, worker_id in paid:
            conn.execute("""INSERT INTO events(event_id,event_type,schema_version,producer_id,
                producer_incarnation,producer_sequence,occurred_at,received_at,origin,
                observation_layer,capture_completeness,work_item_id,call_id,
                worker_instance_id,payload,payload_hash)
                VALUES(%s,'call.dispatched',1,%s,'multi-worker-test',1,now(),now(),
                'synthetic','test','complete',%s,%s,%s,%s,'test-hash')""",
                (str(uuid.uuid4()), f"worker-observation-{uuid.uuid4()}", work_item_id,
                 call_id, worker_id, Jsonb({"destination": "claude"})))
    assert dict(counts) == {"claude": 2, "qwen": 1}
    visible = read_snapshot(dsn, CohortQuery(origin="synthetic"))
    assert {call_id for call_id, _, _ in paid}.issubset(
        {item["call_id"] for item in visible.worker_executions})
    assert len({item["worker_instance_id"] for item in visible.worker_executions
                if item["call_id"] in {row[0] for row in paid}}) == 2


def test_per_root_capacity_blocks_third_start_without_consuming_other_roots(runtime_dsn):
    dsn = runtime_dsn
    policy, version = _activate_two(dsn)
    root = f"root-{uuid.uuid4()}"
    other = f"root-{uuid.uuid4()}"
    calls = [f"call-{uuid.uuid4()}" for _ in range(4)]
    for call in calls[:3]:
        assert admit_call(dsn, _call(root, call, budget=3), policy,
                          policy_version=version, pool="claude", reserve_tokens=100).state == "queued"
    assert admit_call(dsn, _call(other, calls[3]), policy, policy_version=version,
                      pool="claude", reserve_tokens=100).state == "queued"
    owners = [f"owner-{uuid.uuid4()}" for _ in range(3)]
    for owner in owners:
        register_incarnation(dsn, owner, f"worker-{owner}")
    first = claim_dispatch(dsn, pool="claude", incarnation_id=owners[0])
    second = claim_dispatch(dsn, pool="claude", incarnation_id=owners[1])
    assert first and second and first.call_id != second.call_id
    assert claim_dispatch(dsn, pool="claude", incarnation_id=owners[2]) is None
