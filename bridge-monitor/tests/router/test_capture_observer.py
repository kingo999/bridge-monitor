"""Optional observation cannot influence paid routing or the recovery branch."""

import threading
from dataclasses import FrozenInstanceError

import psycopg
import pytest

from tests.router.test_ingress import router_env


def route(env):
    c, _, task, case, receipt = env
    return c.route_work(task, case.task_text, requested_model='claude-test', reserve_tokens=100,
                        output_contract='complete_python_module_v1', verifier_ref=receipt.verifier_ref)


@pytest.mark.parametrize('failure', [False, True])
def test_capture_offer_preserves_dispatch_and_is_not_repeated_on_recovery(router_env, failure):
    coordinator, paid, task, case, receipt = router_env
    with psycopg.connect(coordinator.dsn) as conn:
        before = conn.execute('SELECT count(*) FROM classifier_observations').fetchone()[0]
    observed = []
    class Observer:
        def offer(self, item):
            observed.append(item)
            if failure: raise RuntimeError('optional failure')
    coordinator.route_observer = Observer()
    response = route(router_env)
    assert response['state'] == 'completed' and response['answer'] == 'Claude fixed response'
    assert paid.calls == 1 and len(observed) == 1
    item = observed[0]
    assert item.acceptable() and item.source == case.baseline_source
    assert item.case_digest == receipt.verifier_ref
    assert not hasattr(item, 'tests') and not hasattr(item, 'task')
    task.scope.append('later-mutation')
    assert item.scope == ('analysis',)
    task.scope.pop()
    replay = route(router_env)
    assert replay['call_id'] == response['call_id'] and paid.calls == 1 and len(observed) == 1
    with psycopg.connect(coordinator.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM classifier_observations').fetchone()[0] == before
        assert conn.execute("SELECT count(*) FROM runtime_jobs WHERE execution_mode='production' AND state='completed'").fetchone()[0] == 1


def test_blocked_background_sink_cannot_delay_paid_route(router_env, tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    from tests.classifier.test_hook_capture import settings
    from bridge.classifier.store import CaptureResult
    entered, release = threading.Event(), threading.Event()
    def persist(*a, **kw):
        entered.set()
        release.wait(10)
        return CaptureResult('captured')
    c, paid, *_ = router_env
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=persist)
    c.route_observer = observer
    try:
        result = route(router_env)
        assert entered.wait(2)
        assert not release.is_set() and result['state'] == 'completed' and paid.calls == 1
    finally:
        release.set()
        c.close()


def test_optional_bootstrap_failure_and_other_audiences_are_isolated(tmp_path, monkeypatch):
    from bridge.startup import optional_route_observer
    from bridge.classifier import hook_config
    calls = []
    def broken(**kw):
        calls.append(kw)
        raise ValueError('bad config with secret')
    monkeypatch.setattr(hook_config, 'load_hook_settings', broken)
    for audience in ('claude','shadow'):
        assert optional_route_observer(tmp_path, 'unused', audience) is None
    assert not calls
    assert optional_route_observer(tmp_path, 'unused', 'parent') is None
    assert len(calls) == 1


def test_optional_setup_survives_broken_diagnostics(tmp_path, monkeypatch):
    from bridge import startup
    from bridge.classifier import hook_config
    class Broken:
        def write(self, value): raise OSError('broken pipe')
        def flush(self): pass
    monkeypatch.delenv('BRIDGE_CLASSIFIER_SHADOW_CONFIG', raising=False)
    monkeypatch.setattr(startup.sys, 'stderr', Broken())
    monkeypatch.setattr(hook_config, 'load_hook_settings', lambda **kw: (_ for _ in ()).throw(ValueError()))
    assert startup.optional_route_observer(tmp_path, 'unused', 'parent') is None


def test_inherited_external_manual_config_is_quietly_ignored(tmp_path, monkeypatch, capsys):
    from bridge import startup
    from bridge.classifier import hook_config
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setenv('BRIDGE_CLASSIFIER_SHADOW_CONFIG', str(tmp_path / 'manual.toml'))
    monkeypatch.setattr(hook_config, 'load_hook_settings', lambda **kw: pytest.fail('must not read external file'))
    assert startup.optional_route_observer(project, 'unused', 'parent') is None
    assert capsys.readouterr().err == ''


def test_paired_off_and_on_preserve_bytes_decisions_jobs_and_reservations(router_env):
    from bridge.runtime.policy_startup import activate_policy
    from contracts.policy import validate_policy
    c, paid, task, case, receipt = router_env
    # No Qwen sampling in either phase: observation must remain independent of eligibility.
    policy = c.policy.model_copy(update={'router': c.policy.router.model_copy(update={'sample_percent':0})})
    proposed = policy.model_dump(exclude={'version','source_hash'})
    capabilities = {'claude-test': {'context_tokens':10000}}
    digest = validate_policy(proposed, capabilities).snapshot.source_hash
    activate_policy(c.dsn, proposed, capabilities, approved_hash=digest, operator_id='paired-test')
    observations = []
    class Observer:
        def offer(self, item): observations.append(item)
    def phase(suffix, observer):
        c.route_observer = observer
        packet = task.model_copy(update={'work_item_id':task.work_item_id+suffix,
                                        'call_budget':10, 'token_budget':50000})
        with psycopg.connect(c.dsn) as conn:
            before = conn.execute('SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets WHERE task_id=%s',
                                  (task.task_id,)).fetchone() or (0,0)
        result = c.route_work(packet, case.task_text, requested_model='claude-test', reserve_tokens=100,
                              output_contract='complete_python_module_v1', verifier_ref=receipt.verifier_ref)
        with psycopg.connect(c.dsn) as conn:
            after = conn.execute('SELECT calls_reserved,tokens_reserved FROM runtime_task_budgets WHERE task_id=%s',
                                 (task.task_id,)).fetchone()
            decisions = conn.execute("SELECT route,eligibility,reason,decision_stage,execution_mode,"
                "gate_facts->>'task_sha256',gate_facts->>'baseline_sha256',gate_facts->>'verifier_ref',"
                "gate_facts->>'output_contract' FROM delegation_decisions WHERE work_item_id=%s ORDER BY decision_stage",
                (packet.work_item_id,)).fetchall()
            jobs = conn.execute('SELECT pool,execution_mode,state,requested_model FROM runtime_jobs WHERE work_item_id=%s ORDER BY pool',
                                (packet.work_item_id,)).fetchall()
            bodies = conn.execute('SELECT o.raw_bytes FROM messages m JOIN content_objects o ON o.content_id=m.original_content_id '
                'JOIN calls x USING(call_id) WHERE x.work_item_id=%s ORDER BY m.message_id', (packet.work_item_id,)).fetchall()
        return ({k:result.get(k) for k in ('state','answer','delivered','shadow_state','reason')},
                decisions,jobs,[bytes(b[0]) for b in bodies],tuple(a-b for a,b in zip(after,before)))
    off = phase('-off', None)
    on = phase('-on', Observer())
    assert off == on
    assert off[0]['state'] == 'completed' and off[3] == [case.task_text.encode()]
    assert off[4] == (1,100) and paid.calls == 2 and len(observations) == 1
