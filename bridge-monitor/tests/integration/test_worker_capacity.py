"""Worker capacity must reflect execution ownership, not abandoned queue holds."""
import uuid

import psycopg
import pytest

from bridge.runtime.admission import admit_call
from bridge.runtime.codex_claude import reserve_codex_claude_call
from bridge.runtime.ownership import claim_dispatch, claim_launch, register_incarnation
from bridge.runtime.scheduler import cancel_queued
from contracts.codex_claude import CodexClaudePolicy
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup


@pytest.fixture
def capacity(runtime_dsn):
    with psycopg.connect(runtime_dsn) as conn:
        conn.execute('TRUNCATE cc_receipts,cc_reservations,cc_decisions,cc_records')
    policy, version = _setup(runtime_dsn)
    extension = CodexClaudePolicy(max_active_workers=1)
    def reserve(call):
        return reserve_codex_claude_call(runtime_dsn, call_id=call,
            work_item_id=call, workflow_id=call, profile='sonnet', amount='0.3', policy=extension)
    def queued():
        name = 'capacity-' + str(uuid.uuid4())
        assert reserve(name)['state'] == 'held'
        assert admit_call(runtime_dsn, _call(name, name), policy,
            policy_version=version, pool='claude', reserve_tokens=100).state == 'queued'
        return name
    yield runtime_dsn, reserve, queued
    with psycopg.connect(runtime_dsn) as conn:
        conn.execute('TRUNCATE cc_receipts,cc_reservations,cc_decisions,cc_records')


def test_expired_never_launched_queue_releases_capacity_and_budget_once(capacity):
    dsn, reserve, queued = capacity
    old = queued()
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET queue_deadline_at=now()-interval '1 second' WHERE call_id=%s", (old,))
    following = 'capacity-' + str(uuid.uuid4())
    assert reserve(following)['state'] == 'held'
    # Repeated admission must not debit again, replay, or manufacture provider usage.
    assert reserve(following)['state'] == 'held'
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT state,launch_claimed_at FROM runtime_jobs WHERE call_id=%s', (old,)).fetchone() == ('cancelled', None)
        assert conn.execute('SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets WHERE task_id=%s', (old,)).fetchone() == (0, 0)
        assert conn.execute('SELECT state FROM cc_reservations WHERE call_id=%s', (old,)).fetchone()[0] == 'released'
        assert conn.execute('SELECT count(*) FROM cc_receipts WHERE call_id=%s', (old,)).fetchone()[0] == 0
        audit = conn.execute("SELECT payload FROM cc_records WHERE record_id=%s", ('release:' + old,)).fetchone()[0]
        assert audit['no_charge_confirmed'] is True
        assert audit['reason'] == 'unlaunched_queue_terminal'


def test_cancelled_never_launched_queue_releases_capacity(capacity):
    dsn, reserve, queued = capacity
    old = queued()
    assert cancel_queued(dsn, old)
    assert reserve('capacity-' + str(uuid.uuid4()))['state'] == 'held'
    assert reserve(old)['state'] == 'released'


@pytest.mark.parametrize('state', ['queued', 'active', 'uncertain', 'cancelled'])
def test_live_queue_and_unconfirmed_launched_calls_keep_capacity(capacity, state):
    dsn, reserve, queued = capacity
    old = queued()
    if state != 'queued':
        owner = str(uuid.uuid4())
        register_incarnation(dsn, owner, 'capacity-worker')
        lease = claim_dispatch(dsn, pool='claude', incarnation_id=owner, call_id=old)
        assert claim_launch(dsn, lease)
        with psycopg.connect(dsn) as conn:
            conn.execute("UPDATE runtime_jobs SET state=%s,queue_deadline_at=now()-interval '1 hour' WHERE call_id=%s", (state, old))
    assert reserve('capacity-' + str(uuid.uuid4())) == {'state': 'rejected', 'reason': 'worker_limit'}
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT state FROM cc_reservations WHERE call_id=%s', (old,)).fetchone()[0] == 'held'


def test_missing_runtime_job_is_not_proof_of_no_charge(capacity):
    dsn, reserve, _ = capacity
    old = 'capacity-' + str(uuid.uuid4())
    assert reserve(old)['state'] == 'held'
    assert reserve('capacity-' + str(uuid.uuid4()))['reason'] == 'worker_limit'


def test_dispatch_intention_cannot_launch_after_queue_expiry(capacity):
    from bridge.runtime.codex_claude import claim_codex_claude_dispatch
    from bridge.runtime.ownership import abort_unlaunched
    dsn, reserve, queued = capacity
    old = queued()
    assert claim_codex_claude_dispatch(dsn, old)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET queue_deadline_at=now()-interval '1 second' WHERE call_id=%s", (old,))
    assert reserve('capacity-'+str(uuid.uuid4()))['state'] == 'held'
    owner = str(uuid.uuid4())
    register_incarnation(dsn, owner, 'expired-intent-worker')
    assert claim_dispatch(dsn, pool='claude', incarnation_id=owner, call_id=old) is None


def test_verified_prelaunch_abort_with_lease_returns_capacity(capacity):
    from bridge.runtime.ownership import abort_unlaunched
    dsn, reserve, queued = capacity
    old = queued()
    owner = str(uuid.uuid4())
    register_incarnation(dsn, owner, 'prelaunch-abort-worker')
    lease = claim_dispatch(dsn, pool='claude', incarnation_id=owner, call_id=old)
    assert abort_unlaunched(dsn, lease)
    assert not claim_launch(dsn, lease)
    assert reserve('capacity-'+str(uuid.uuid4()))['state'] == 'held'
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets WHERE task_id=%s',(old,)).fetchone() == (0,0)


def test_queue_monitor_expires_abandoned_request_without_new_admission(capacity):
    import time
    from bridge.runtime.queue_maintenance import QueueExpiryMonitor
    dsn, _, queued = capacity
    old = queued()
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET queue_deadline_at=now()-interval '1 second' WHERE call_id=%s", (old,))
    monitor = QueueExpiryMonitor(dsn, interval_seconds=0.05)
    monitor.start()
    try:
        until = time.monotonic() + 3
        while time.monotonic() < until:
            with psycopg.connect(dsn) as conn:
                if conn.execute('SELECT state FROM cc_reservations WHERE call_id=%s', (old,)).fetchone()[0] == 'released':
                    break
            time.sleep(0.02)
        else:
            pytest.fail('monitor did not release the expired never-launched request')
        assert monitor.snapshot()['last_success_at'] is not None
        assert monitor.snapshot()['released_total'] == 1
    finally:
        monitor.close()
    assert monitor.snapshot()['running'] is False


def test_concurrent_queue_sweeps_do_not_double_return_budget(capacity):
    from concurrent.futures import ThreadPoolExecutor
    from bridge.runtime.codex_claude import reconcile_expired_codex_claude_queue
    dsn, _, queued = capacity
    old = queued()
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET queue_deadline_at=now()-interval '1 second' WHERE call_id=%s", (old,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reconcile_expired_codex_claude_queue, [dsn, dsn]))
    assert sum(len(r['released']) for r in results) == 1
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets WHERE task_id=%s', (old,)).fetchone() == (0, 0)


def test_dashboard_runs_queue_monitor_and_protects_its_status(capacity):
    import time
    from fastapi.testclient import TestClient
    from service.http.app import create_app
    dsn, _, queued = capacity
    old = queued()
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET queue_deadline_at=now()-interval '1 second' WHERE call_id=%s", (old,))
    app = create_app(dsn, producer_tokens={'worker':'test-producer-token'},
        operator_token='test-operator-token', payload_limit_bytes=100000)
    with TestClient(app) as client:
        assert client.get('/api/v1/queue-maintenance').status_code == 401
        until = time.monotonic() + 3
        while time.monotonic() < until:
            response = client.get('/api/v1/queue-maintenance', headers={'Authorization':'Bearer test-operator-token'})
            assert response.status_code == 200
            if response.json()['released_total'] == 1:
                break
            time.sleep(0.02)
        else:
            pytest.fail('dashboard did not start queue cleanup')
        assert response.json()['running'] is True
    assert app.state.queue_monitor.snapshot()['running'] is False


def test_queue_monitor_recovers_from_a_transient_database_failure(capacity, monkeypatch):
    import time
    from bridge.runtime import queue_maintenance
    dsn, _, queued = capacity
    old = queued()
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET queue_deadline_at=now()-interval '1 second' WHERE call_id=%s", (old,))
    sweep = queue_maintenance.reconcile_expired_codex_claude_queue
    attempts = []
    def interrupted_then_real(selected):
        attempts.append(selected)
        if len(attempts) == 1:
            raise psycopg.OperationalError('synthetic outage')
        return sweep(selected)
    monkeypatch.setattr(queue_maintenance, 'reconcile_expired_codex_claude_queue', interrupted_then_real)
    monitor = queue_maintenance.QueueExpiryMonitor(dsn, interval_seconds=0.02)
    monitor.start()
    try:
        until = time.monotonic() + 3
        while monitor.snapshot()['released_total'] == 0 and time.monotonic() < until:
            time.sleep(0.02)
        status = monitor.snapshot()
        assert status['released_total'] == 1
        assert status['failed_sweeps'] == 1
        assert status['last_error_type'] is None
    finally:
        monitor.close()


@pytest.mark.parametrize('terminated,reconciliation_error', [(True,False),(False,False),(True,True)])
def test_bounded_analysis_exit_reconciles_only_confirmed_termination(capacity, tmp_path, monkeypatch, terminated, reconciliation_error):
    from bridge.adapters.base import AdapterResult
    from bridge.mcp_server import BridgeCoordinator
    from bridge.telemetry.emitter import TelemetryEmitter
    from bridge.telemetry.spool import DurableSpool
    from psycopg.types.json import Jsonb
    from service.ingest.store import ingest_batch
    dsn, reserve, _ = capacity
    monkeypatch.setenv('BRIDGE_MONITOR_SYSTEM_TOKEN', 'synthetic-capacity-token')
    base, version = _setup(dsn)
    extension = CodexClaudePolicy(enabled=True, max_active_workers=1)
    base = base.model_copy(update={'codex_claude': extension})
    class Collector:
        def ingest_batch(self, producer, events, content):
            return ingest_batch(dsn, producer, events, content)
    emitter = TelemetryEmitter(DurableSpool(tmp_path/'spool.sqlite', limit_bytes=100000),
        Collector(), producer_id='capacity-'+str(uuid.uuid4()), incarnation='boot')
    class FailedAnalysis:
        profile_models = ('claude-sonnet-5-5',)
        def run_profile(self, lease, task, prompt, emitter, *, profile):
            return AdapterResult(state='uncertain', reported_model='claude-sonnet-5-5',
                uncertainty='execution_outcome_unconfirmed', terminated=terminated)
    service = BridgeCoordinator(dsn, base, version, emitter, claude_adapter=FailedAnalysis())
    if reconciliation_error:
        def unavailable(*args, **kw):
            raise psycopg.OperationalError('synthetic reconciliation outage')
        monkeypatch.setattr('bridge.runtime.reconciliation.reconcile_execution', unavailable)
    old = 'capacity-'+str(uuid.uuid4())
    request = _call(old, old)
    assert reserve(old)['state'] == 'held'
    assert service.ingress.submit(request, prompt='supplied analysis only', pool='claude', reserve_tokens=100).state == 'queued'
    with psycopg.connect(dsn) as conn:
        conn.execute('UPDATE cc_reservations SET work_item_id=%s WHERE call_id=%s', (request.task.work_item_id, old))
        conn.execute('INSERT INTO cc_decisions(decision_id,work_item_id,task_id,prompt_sha256,payload) VALUES(%s,%s,%s,%s,%s)',
            (str(uuid.uuid4()), request.task.work_item_id, old, '0'*64,
             Jsonb({'policy_snapshot':extension.model_dump(mode='json'),'selected_profile':'sonnet'})))
    try:
        result = service.run_one('claude', call_id=old)
        confirmed = terminated and not reconciliation_error
        assert result['state'] == ('terminated' if confirmed else 'uncertain')
        if confirmed:
            assert result['automatic_retry'] is False
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT state,slot_held FROM runtime_jobs WHERE call_id=%s', (old,)).fetchone() == (('terminated', False) if confirmed else ('uncertain', True))
            assert conn.execute('SELECT state FROM cc_reservations WHERE call_id=%s', (old,)).fetchone()[0] == 'held'
            assert conn.execute('SELECT count(*) FROM cc_receipts WHERE call_id=%s', (old,)).fetchone()[0] == 0
        assert reserve('capacity-'+str(uuid.uuid4()))['state'] == ('held' if confirmed else 'rejected')
    finally:
        service.close()
