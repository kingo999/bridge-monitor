"""Exact binding and saved pages must work while the classifier endpoint is stopped."""

from datetime import timedelta
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from bridge.classifier import store
from tests.classifier.test_hook_capture import NOW, settings, snapshot
from tests.classifier.fakes import FakeOllama, reply_json


def observation(dsn, tmp_path, index=0, *, at=NOW):
    from bridge.classifier.hook_capture import prepare_capture
    cfg = settings(tmp_path)
    index = f'{uuid.uuid4().hex}-{index}'
    item = snapshot(task_id=f'ht-{index}', case_task_id=f'ht-{index}', work_item_id=f'hw-{index}',
                    decision_id=f'hd-{index}', paid_call_id=f'hc-{index}', captured_at=at,
                    registered_at=at-timedelta(seconds=1))
    cap = prepare_capture(item, cfg)
    # Populate a backlog without weakening production accounting or making model calls.
    obs = store.capture(dsn, cap, cfg.shadow_config(), now=at).observation_id
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE classifier_observations SET state='dispatching' WHERE observation_id=%s", (obs,))
    store.finish(dsn, obs, state='completed', fields={'valid': True, 'rec_route': 'paid',
                 'completed_at': at}, response=b'original', response_complete=True)
    return obs, cap.facts_provenance['route_work_binding']


def publish(dsn, b, *, route='paid', change=None):
    facts = {k: b[k] for k in ('task_sha256', 'baseline_sha256', 'verifier_ref', 'output_contract')}
    facts.update(change or {})
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO tasks(task_id,origin,scope,created_at) VALUES (%s,'observed','{}',%s) ON CONFLICT DO NOTHING", (b['task_id'], NOW))
        conn.execute("INSERT INTO work_items(work_item_id,task_id,origin,created_at) VALUES (%s,%s,'observed',%s) ON CONFLICT DO NOTHING", (b['work_item_id'], b['task_id'], NOW))
        conn.execute("INSERT INTO calls(call_id,work_item_id,created_at,execution_mode) VALUES (%s,%s,%s,'production') ON CONFLICT DO NOTHING", (b['paid_call_id'], b['work_item_id'], NOW))
        conn.execute("INSERT INTO delegation_decisions(decision_id,work_item_id,child_call_id,route,eligibility,gate_facts,decided_at,decision_stage,execution_mode) "
            "VALUES (%s,%s,%s,%s,'eligible',%s,%s,'parent_router','production')",
            (b['decision_id'], b['work_item_id'], b['paid_call_id'], route, Jsonb(facts), NOW))


def row(dsn, obs):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute('SELECT * FROM classifier_observations WHERE observation_id=%s', (obs,)).fetchone()


@pytest.mark.parametrize('key', ['task_sha256','baseline_sha256','verifier_ref','output_contract'])
def test_immediate_and_late_reject_each_gate_mismatch(cdsn, tmp_path, key):
    from bridge.classifier.route_links import immediate_link, reconcile_page
    obs, b = observation(cdsn, tmp_path)
    publish(cdsn, b, change={key:'wrong'})
    assert immediate_link(cdsn, row(cdsn, obs))['actual_route_source']['status'] == 'binding_mismatch'
    assert reconcile_page(cdsn)['linked'] == 0
    assert row(cdsn, obs)['actual_route'] is None


def test_exact_late_fill_preserves_outcome_and_uses_no_model(cdsn, tmp_path):
    from bridge.classifier.route_links import reconcile_page
    obs, b = observation(cdsn, tmp_path)
    before = row(cdsn, obs)
    assert reconcile_page(cdsn)['linked'] == 0
    publish(cdsn, b, route='local')
    from bridge.classifier.runner import ShadowRunner
    fake = FakeOllama(mode='down')
    with ShadowRunner(cdsn, settings(tmp_path).shadow_config(), fake.classifier()) as runner:
        assert runner.process_one().status == 'unavailable'
    after = row(cdsn, obs)
    assert after['actual_route'] == 'code_local'
    assert after['actual_route_source']['binding_sha256'] == b['sha256']
    assert fake.chat_calls == 0
    assert {k:v for k,v in after.items() if not k.startswith('actual_route')} == {
        k:v for k,v in before.items() if not k.startswith('actual_route')}


def test_saved_cursor_passes_unresolved_backlog_then_wraps(cdsn, tmp_path):
    from bridge.classifier.route_links import reconcile_page
    items = [observation(cdsn, tmp_path, i, at=NOW+timedelta(seconds=i)) for i in range(130)]
    publish(cdsn, items[-1][1])
    first = reconcile_page(cdsn)
    assert (first['examined'], first['linked']) == (64, 0)
    with psycopg.connect(cdsn) as conn:
        saved = conn.execute('SELECT last_id,upper_id FROM classifier_route_link_progress').fetchone()
    assert all(saved)
    assert reconcile_page(cdsn)['examined'] == 64
    assert reconcile_page(cdsn)['linked'] == 1
    publish(cdsn, items[0][1])
    assert reconcile_page(cdsn)['linked'] == 1


@pytest.mark.parametrize('change', ['decision_id','paid_call_id','work_item_id','task_id','mode','stage','pending'])
def test_never_substitutes_another_submission(cdsn, tmp_path, change):
    from bridge.classifier.route_links import immediate_link, reconcile_page
    obs, b = observation(cdsn, tmp_path)
    other = dict(b)
    if change in other: other[change] += '-wrong'
    publish(cdsn, other, route='pending' if change == 'pending' else 'paid')
    if change in ('mode','stage'):
        with psycopg.connect(cdsn) as conn:
            column = 'execution_mode' if change == 'mode' else 'decision_stage'
            conn.execute(f"UPDATE delegation_decisions SET {column}=%s WHERE decision_id=%s",
                ('shadow' if change == 'mode' else 'claude_child', b['decision_id']))
    assert 'actual_route' not in immediate_link(cdsn, row(cdsn, obs))
    assert reconcile_page(cdsn)['linked'] == 0


def test_reconcile_failure_rolls_back_fills_and_cursor(cdsn, tmp_path, monkeypatch):
    from bridge.classifier import route_links
    obs, b = observation(cdsn, tmp_path)
    publish(cdsn, b)
    original = route_links.validate_link
    def broken(*args):
        original(*args)
        raise RuntimeError('injected')
    monkeypatch.setattr(route_links, 'validate_link', broken)
    with pytest.raises(RuntimeError): route_links.reconcile_page(cdsn)
    assert row(cdsn, obs)['actual_route'] is None
    with psycopg.connect(cdsn) as conn:
        assert conn.execute('SELECT last_id,upper_id FROM classifier_route_link_progress').fetchone() == (None,None)


def test_bad_decision_metadata_does_not_starve_later_rows(cdsn, tmp_path):
    from bridge.classifier.route_links import reconcile_page
    bad, binding = observation(cdsn, tmp_path, 0)
    good, next_binding = observation(cdsn, tmp_path, 1, at=NOW+timedelta(seconds=1))
    publish(cdsn, binding)
    publish(cdsn, next_binding)
    original = {k:binding[k] for k in ('task_sha256','baseline_sha256','verifier_ref','output_contract')}
    try:
        with psycopg.connect(cdsn) as conn:
            conn.execute('UPDATE delegation_decisions SET gate_facts=%s WHERE decision_id=%s',
                         (Jsonb([]),binding['decision_id']))
        result = reconcile_page(cdsn)
        assert result['examined'] == 2 and result['linked'] == 1
        assert row(cdsn,bad)['actual_route'] is None and row(cdsn,good)['actual_route'] == 'paid'
    finally:
        # cdsn clears classifier evidence, not production fixtures used by later cohort tests.
        with psycopg.connect(cdsn) as conn:
            conn.execute('UPDATE delegation_decisions SET gate_facts=%s WHERE decision_id=%s',
                         (Jsonb(original),binding['decision_id']))
