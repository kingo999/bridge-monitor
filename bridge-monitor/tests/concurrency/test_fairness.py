import uuid

import psycopg

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from tests.concurrency.test_limits import _activate_two
from tests.integration.test_admission import _call


def test_roots_rotate_while_each_root_remains_fifo(runtime_dsn):
    dsn = runtime_dsn
    policy, version = _activate_two(dsn)
    root_a, root_b = "a-" + str(uuid.uuid4()), "b-" + str(uuid.uuid4())
    calls_a = [f"call-{uuid.uuid4()}" for _ in range(2)]
    call_b = f"call-{uuid.uuid4()}"
    for call in calls_a:
        assert admit_call(dsn, _call(root_a, call), policy, policy_version=version,
                          pool="claude", reserve_tokens=100).state == "queued"
    assert admit_call(dsn, _call(root_b, call_b), policy, policy_version=version,
                      pool="claude", reserve_tokens=100).state == "queued"
    with psycopg.connect(dsn) as conn:
        order = [row[0] for row in conn.execute("SELECT call_id FROM runtime_jobs WHERE task_id=%s ORDER BY admitted_at,call_id", (root_a,))]
    owners = [f"owner-{uuid.uuid4()}" for _ in range(2)]
    for owner in owners:
        register_incarnation(dsn, owner, f"worker-{owner}")
    first = claim_dispatch(dsn, pool="claude", incarnation_id=owners[0])
    second = claim_dispatch(dsn, pool="claude", incarnation_id=owners[1])
    assert first.call_id == order[0]
    assert second.call_id == call_b
