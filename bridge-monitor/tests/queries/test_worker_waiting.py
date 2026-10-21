import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from bridge.runtime.admission import admit_call
from bridge.runtime.ownership import claim_dispatch, register_incarnation
from contracts.queries import CohortQuery
from service.queries.snapshot import read_snapshot
from tests.integration.test_admission import _call
from tests.concurrency.test_limits import _activate_two


@pytest.mark.parametrize('child_state,mode,matching_parent,parent_state,expected', [
    ('queued', 'production', True, 'active', True),
    ('active', 'production', True, 'active', True),
    ('completed', 'production', True, 'active', False),
    ('fallback', 'production', True, 'active', False),
    ('uncertain', 'production', True, 'active', False),
    ('active', 'shadow', True, 'active', False),
    ('active', 'production', False, 'active', False),
    ('active', 'production', True, 'uncertain', False),
])
def test_waiting_requires_exact_live_production_child(runtime_dsn, child_state, mode,
                                                     matching_parent, parent_state, expected):
    dsn = runtime_dsn
    root, parent_id, child_id, owner = [str(uuid.uuid4()) for _ in range(4)]
    parent = _call(root, parent_id)
    policy, version = _activate_two(dsn)
    assert admit_call(dsn, parent, policy, policy_version=version,
                      pool='claude', reserve_tokens=100).state == 'queued'
    register_incarnation(dsn, owner, 'waiting-worker-'+owner)
    assert claim_dispatch(dsn, pool='claude', incarnation_id=owner)
    child = parent.model_copy(update={'call_id': child_id,
        'parent_call_id': parent_id if matching_parent else 'unrelated-parent'})
    assert admit_call(dsn, child, policy, policy_version=version, pool='qwen',
                      reserve_tokens=100,
                      local_cutoff_at=datetime.now(timezone.utc)+timedelta(seconds=60)).state == 'queued'
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,created_at) VALUES(%s,'observed',now())", (root,))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) "
                     "VALUES(%s,%s,'observed',now())", (parent.task.work_item_id, root))
        conn.execute('UPDATE runtime_jobs SET state=%s,execution_mode=%s,slot_held=%s '
                     'WHERE call_id=%s', (child_state, mode, child_state != 'queued', child_id))
        conn.execute('UPDATE runtime_jobs SET state=%s WHERE call_id=%s', (parent_state, parent_id))
    snapshot = read_snapshot(dsn, CohortQuery(origin='observed'))
    execution = next(e for e in snapshot.worker_executions if e['call_id'] == parent_id)
    assert execution.get('waiting_on_local') is expected
