import json
import uuid

import psycopg
import pytest

from bridge.adapters.codex_ingress import CodexIngress
from bridge.adapters.pinned_dispatch import prepare_pinned_prompt, submit_pinned_context
from bridge.runtime.ownership import claim_dispatch, claim_launch, register_incarnation
from bridge.telemetry.emitter import TelemetryEmitter
from bridge.telemetry.spool import DurableSpool
from tests.adapters.test_ingress import Collector
from tests.contracts.test_pinned_context import build
from tests.integration.test_admission import _call
from tests.integration.test_ownership import _setup


@pytest.fixture
def prepared(runtime_dsn, tmp_path):
    policy, version = _setup(runtime_dsn)
    emitter = TelemetryEmitter(DurableSpool(tmp_path / 'pinned.sqlite', limit_bytes=100000),
        Collector(runtime_dsn), producer_id=f'pinned-{uuid.uuid4()}', incarnation='test')
    return CodexIngress(runtime_dsn, emitter, policy, version)


def inputs():
    call = _call(f'task-{uuid.uuid4()}', f'call-{uuid.uuid4()}')
    bundle = build(task_id=call.task.task_id, work_item_id=call.task.work_item_id)
    return call, dict(context=bundle, expected_context_sha256=bundle.context_sha256,
                      prompt='Analyze supplied evidence', max_context_bytes=1024, reserve_tokens=100)


def test_exact_prompt_is_captured_and_retry_uses_existing_admission(prepared):
    call, args = inputs()
    first = submit_pinned_context(prepared, call, **args)
    second = submit_pinned_context(prepared, call, **args)
    assert first == second and first.state == 'queued'
    expected = prepare_pinned_prompt(request=call, max_prompt_bytes=100000,
                                     **{key: value for key, value in args.items() if key != 'reserve_tokens'})
    with psycopg.connect(prepared.dsn) as conn:
        body, launch, lease = conn.execute('SELECT c.raw_bytes,j.launch_claimed_at,j.lease_id '
            'FROM runtime_jobs j JOIN content_objects c ON c.content_id=j.prompt_content_id '
            'WHERE j.call_id=%s', (call.call_id,)).fetchone()
        assert bytes(body) == expected.encode() and launch is None and lease is None
        assert json.loads(bytes(body))['pinned_context']['context_sha256'] == args['expected_context_sha256']
        assert conn.execute('SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets '
                            'WHERE task_id=%s', (call.task.task_id,)).fetchone() == (1, 100)
        assert conn.execute('SELECT count(*) FROM messages WHERE call_id=%s', (call.call_id,)).fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['digest', 'budget', 'identity'])
def test_verification_failure_never_captures_or_admits(prepared, failure):
    call, args = inputs()
    if failure == 'digest':
        args['expected_context_sha256'] = '0' * 64
    elif failure == 'budget':
        args['max_context_bytes'] = 1
    else:
        call.task.work_item_id = 'other'
    with pytest.raises(ValueError):
        submit_pinned_context(prepared, call, **args)
    with psycopg.connect(prepared.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM runtime_jobs WHERE call_id=%s', (call.call_id,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM messages WHERE call_id=%s', (call.call_id,)).fetchone()[0] == 0


def test_changed_instruction_on_retry_is_rejected(prepared):
    call, args = inputs()
    assert submit_pinned_context(prepared, call, **args).state == 'queued'
    with pytest.raises(ValueError, match='prompt mismatch'):
        submit_pinned_context(prepared, call, **(args | {'prompt': 'Changed instruction'}))


def test_existing_queue_limit_and_zero_call_budget_are_not_bypassed(prepared):
    call, args = inputs()
    assert submit_pinned_context(prepared, call, **args).state == 'queued'
    other, other_args = inputs()
    assert submit_pinned_context(prepared, other, **other_args).reason == 'queue_full'
    with psycopg.connect(prepared.dsn) as conn:
        conn.execute("UPDATE runtime_jobs SET state='cancelled' WHERE call_id=%s", (call.call_id,))
    other.task.call_budget = 0
    assert submit_pinned_context(prepared, other, **other_args).reason == 'task_pin_conflict'
    third, third_args = inputs()
    third.task.call_budget = 0
    assert submit_pinned_context(prepared, third, **third_args).reason == 'root_budget_exhausted'


def test_existing_lease_and_launch_claim_are_reused_without_provider_execution(prepared):
    call, args = inputs()
    assert submit_pinned_context(prepared, call, **args).state == 'queued'
    owner = f'test-{uuid.uuid4()}'
    register_incarnation(prepared.dsn, owner, 'fake-test-worker')
    lease = claim_dispatch(prepared.dsn, pool='claude', incarnation_id=owner, call_id=call.call_id)
    assert lease is not None
    assert claim_dispatch(prepared.dsn, pool='claude', incarnation_id=owner, call_id=call.call_id) == lease
    # These are scratch database transitions only; no provider/worker is invoked.
    assert claim_launch(prepared.dsn, lease)
    assert not claim_launch(prepared.dsn, lease)


def test_current_policy_payload_limit_is_checked_before_capture(prepared):
    from bridge.runtime.policy_startup import active_policy, activate_policy
    from contracts.policy import validate_policy

    proposed = active_policy(prepared.dsn).model_dump(exclude={'version', 'source_hash'})
    proposed['deployment']['payload_limit_bytes'] = 100
    capabilities = {'claude-test': {'context_tokens': 10000}}
    digest = validate_policy(proposed, capabilities).snapshot.source_hash
    # Payload limits are restart-only. Use the existing startup path in this
    # disposable test database rather than pretending a soft reload can change it.
    activate_policy(prepared.dsn, proposed, capabilities, approved_hash=digest, operator_id='test')
    call, args = inputs()
    with pytest.raises(ValueError, match='prompt.*budget'):
        submit_pinned_context(prepared, call, **args)
    with psycopg.connect(prepared.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM messages WHERE call_id=%s', (call.call_id,)).fetchone()[0] == 0


def test_policy_reload_during_submission_fails_closed(prepared, monkeypatch):
    from bridge.runtime.policy_reload import reload_policy
    from bridge.runtime.policy_startup import active_policy
    from contracts.policy import validate_policy

    monkeypatch.setenv('BRIDGE_MONITOR_OPERATOR_TOKEN', 'scratch-test-token')
    original_refresh = prepared.refresh_policy
    refreshes = 0

    def refresh():
        nonlocal refreshes
        refreshes += 1
        if refreshes == 2:
            proposed = active_policy(prepared.dsn).model_dump(exclude={'version', 'source_hash'})
            proposed['deployment']['queue_deadline_seconds'] += 1
            capabilities = {'claude-test': {'context_tokens': 10000}}
            digest = validate_policy(proposed, capabilities).snapshot.source_hash
            assert reload_policy(prepared.dsn, proposed, capabilities, approved_hash=digest,
                                 operator_id='test', auth_token='scratch-test-token').status == 'activated'
        return original_refresh()

    monkeypatch.setattr(prepared, 'refresh_policy', refresh)
    call, args = inputs()
    decision = submit_pinned_context(prepared, call, **args)
    assert decision.state == 'rejected' and decision.reason == 'policy_changed'
    with psycopg.connect(prepared.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM runtime_jobs WHERE call_id=%s', (call.call_id,)).fetchone()[0] == 0
