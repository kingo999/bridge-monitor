"""Schema absence, fault isolation, restart accounting and indexed backlog bounds."""

import json
import uuid
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from bridge.classifier import store
from bridge.classifier.runner import ShadowRunner
from bridge.classifier.hook_capture import prepare_capture
from bridge.classifier.hook_store import capture_hook
from tests.classifier.fakes import FakeOllama, reply_json
from tests.classifier.test_hook_capture import settings, snapshot
from tests.classifier.test_route_links import observation, publish, row
from tests.classifier.test_schema_and_views import isolated


def test_hook_expiry_stays_600_seconds_when_manual_age_is_longer(cdsn, tmp_path):
    cfg = settings(tmp_path)
    now = store.utcnow()
    item = snapshot(captured_at=now-timedelta(seconds=601), registered_at=now-timedelta(seconds=602))
    cap = prepare_capture(item, cfg)
    shadow = cfg.shadow_config()
    shadow.max_queue_age_seconds = 3600
    obs = store.capture(cdsn, cap, shadow, now=item.captured_at).observation_id
    fake = FakeOllama()
    with ShadowRunner(cdsn, shadow, fake.classifier()) as runner:
        assert runner.process_one().status == 'idle'
    assert row(cdsn, obs)['skip_reason'] == 'expired_unprocessed' and fake.chat_calls == 0


def test_hook_lookup_failure_preserves_response_usage_and_later_reconciles(cdsn, tmp_path, monkeypatch):
    from bridge.classifier import route_links
    cfg = settings(tmp_path)
    uid = uuid.uuid4().hex
    cap = prepare_capture(snapshot(task_id=uid, case_task_id=uid, work_item_id='w'+uid,
        decision_id='d'+uid, paid_call_id='c'+uid, captured_at=store.utcnow()), cfg)
    obs = store.capture(cdsn, cap, cfg.shadow_config()).observation_id
    fake = FakeOllama(reply_json('code_local', 'code'))
    original = route_links.immediate_link
    monkeypatch.setattr(route_links, 'immediate_link', lambda *a: (_ for _ in ()).throw(psycopg.errors.QueryCanceled()))
    with ShadowRunner(cdsn, cfg.shadow_config(), fake.classifier()) as runner:
        assert runner.process_one().state == 'completed'
    saved = row(cdsn, obs)
    assert saved['actual_route'] is None and saved['actual_route_source']['status'] == 'lookup_failed'
    assert saved['response_content_id'] and saved['input_tokens'] == 700 and saved['rec_route'] == 'code_local'
    assert fake.chat_calls == 1
    monkeypatch.setattr(route_links, 'immediate_link', original)
    publish(cdsn, cap.facts_provenance['route_work_binding'])
    assert route_links.reconcile_page(cdsn)['linked'] == 1
    assert row(cdsn, obs)['actual_route'] == 'paid' and fake.chat_calls == 1


def test_repeated_reconciliation_timeouts_suspend_but_do_not_block_classification(cdsn, cfg, monkeypatch):
    from bridge.classifier import ingest, route_links
    attempts = []
    def timeout(*a):
        attempts.append(1)
        raise psycopg.errors.QueryCanceled()
    monkeypatch.setattr(route_links, 'reconcile_page', timeout)
    for i in range(3):
        ingest.capture_text(cdsn, cfg, origin_kind='controlled', origin_ref=f'timeout-{i}',
            request='Hi', facts={'paid_allowed':True})
    fake = FakeOllama()
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        for _ in range(3): assert runner.process_one().state == 'completed'
        assert runner.link_progress['suspended'] and attempts == [1,1]
    assert fake.chat_calls == 3


def test_missing_008_keeps_manual_operation_usable(isolated, tmp_path):
    from database.migrate import MIGRATIONS
    from bridge.classifier import ingest, route_links
    with psycopg.connect(isolated) as conn:
        conn.execute((MIGRATIONS / '007_classifier_shadow.sql').read_text())
    cfg = settings(tmp_path)
    cap = prepare_capture(snapshot(captured_at=store.utcnow()), cfg)
    from bridge.classifier.hook_store import CaptureDatabaseError
    with pytest.raises(CaptureDatabaseError) as error:
        capture_hook(isolated, cap, cfg.shadow_config(), captured_at=store.utcnow())
    assert isinstance(error.value.__cause__, psycopg.errors.UndefinedTable)
    ingest.capture_text(isolated, cfg.shadow_config(), origin_kind='controlled', origin_ref='manual',
                       request='Hi', facts={'paid_allowed':True})
    fake = FakeOllama()
    with ShadowRunner(isolated, cfg.shadow_config(), fake.classifier()) as runner:
        assert runner.process_one().state == 'completed' and runner.link_progress['suspended']
    assert fake.chat_calls == 1


def test_prior_delivery_metadata_prevents_capture_but_later_delivery_does_not(cdsn, tmp_path):
    cfg = settings(tmp_path)
    for earlier in (True, False):
        uid = uuid.uuid4().hex
        item = snapshot(task_id=uid, case_task_id=uid, work_item_id='w'+uid,
                        decision_id='d'+uid, paid_call_id='c'+uid, captured_at=store.utcnow())
        cap = prepare_capture(item, cfg)
        b = cap.facts_provenance['route_work_binding']
        publish(cdsn, b)
        with psycopg.connect(cdsn) as conn:
            conn.execute('INSERT INTO results(result_id,call_id,delivered_at) VALUES (%s,%s,%s)',
                ('r'+uid, b['paid_call_id'], item.captured_at+timedelta(seconds=-1 if earlier else 1)))
        result = capture_hook(cdsn, cap, cfg.shadow_config(), captured_at=item.captured_at)
        assert (result.skip_reason == 'solution_already_available') if earlier else result.state == 'captured'


def test_large_backlog_uses_partial_index_and_advances_over_incomplete_bindings(cdsn, tmp_path):
    from bridge.classifier.route_links import PENDING, reconcile_page
    obs, _ = observation(cdsn, tmp_path)
    with psycopg.connect(cdsn) as conn:
        # Metadata-only copies, each deliberately incomplete, exercise the real index page.
        conn.execute("""INSERT INTO classifier_observations
            (observation_id,source_key,origin_kind,origin_ref,task_id,work_item_id,
             request_sha256,source_sha256,input_bytes,facts,facts_captured_at,facts_provenance,
             contract_version,rubric_version,rubric_sha256,profile_id,profile,requested_model,
             expected_digest,state,captured_at)
            SELECT 'index-'||g,'index-key-'||g,origin_kind,origin_ref,task_id,work_item_id,
             request_sha256,source_sha256,0,facts,facts_captured_at,'{"capture_method":"route_work"}'::jsonb,
             contract_version,rubric_version,rubric_sha256,profile_id,profile,requested_model,
             expected_digest,'completed',captured_at+g*interval '1 millisecond'
            FROM classifier_observations CROSS JOIN generate_series(1,10000) g
            WHERE observation_id=%s""", (obs,))
        conn.execute('ANALYZE classifier_observations')
        plan = conn.execute(f'EXPLAIN (ANALYZE,FORMAT JSON) SELECT observation_id,captured_at '
            f'FROM classifier_observations WHERE {PENDING} ORDER BY captured_at,observation_id LIMIT 64').fetchone()[0]
        assert 'classifier_pending_bridge_link_idx' in json.dumps(plan)
        assert plan[0]['Plan']['Actual Rows'] == 64
    first = reconcile_page(cdsn)
    second = reconcile_page(cdsn)
    assert first['examined'] == second['examined'] == 64 and first['linked'] == second['linked'] == 0
    with psycopg.connect(cdsn) as conn:
        assert conn.execute('SELECT last_id FROM classifier_route_link_progress').fetchone()[0] is not None


def test_008_is_additive_and_reapplying_cannot_replenish_budget(isolated):
    from database.migrate import migrate
    with psycopg.connect(isolated) as conn:
        before = conn.execute('SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM work_items), '
                              '(SELECT count(*) FROM content_objects)').fetchone()
    assert migrate(isolated) == ['007_classifier_shadow', '008_classifier_route_capture', '009_classifier_normal_capture',
                                 '010_direct_codex', '011_classifier_capture_exclusions']
    with psycopg.connect(isolated) as conn:
        assert conn.execute('SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM work_items), '
                            '(SELECT count(*) FROM content_objects)').fetchone() == before
        conn.execute('UPDATE classifier_hook_capture_budget SET consumed_rows=1,consumed_bytes=100')
    assert migrate(isolated) == []
    with psycopg.connect(isolated) as conn:
        assert conn.execute('SELECT consumed_rows,consumed_bytes FROM classifier_hook_capture_budget').fetchone() == (1,100)


def test_success_resets_timeout_streak_without_resetting_total_diagnostics(cdsn, cfg, monkeypatch):
    from bridge.classifier import route_links
    results = iter([psycopg.errors.QueryCanceled(), {'examined':0,'linked':0}, psycopg.errors.QueryCanceled()])
    def sequence(*args):
        result = next(results)
        if isinstance(result, Exception): raise result
        return result
    monkeypatch.setattr(route_links, 'reconcile_page', sequence)
    fake = FakeOllama(mode='down')
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        for _ in range(3): assert runner.process_one().status == 'unavailable'
        assert runner.link_progress['timeouts'] == 2 and not runner.link_progress['suspended']


def test_broken_diagnostic_stream_cannot_abort_normal_classification(cdsn, cfg, monkeypatch):
    from bridge.classifier import ingest, route_links, runner as module
    class BrokenStream:
        def write(self, text): raise OSError('broken pipe')
        def flush(self): pass
    monkeypatch.setattr(module.sys, 'stderr', BrokenStream())
    monkeypatch.setattr(route_links, 'reconcile_page', lambda *a: (_ for _ in ()).throw(ValueError('missing progress')))
    ingest.capture_text(cdsn, cfg, origin_kind='controlled', origin_ref='diagnostic-error',
                       request='Hi', facts={'paid_allowed':True})
    fake = FakeOllama()
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        assert runner.process_one().state == 'completed'
    assert fake.chat_calls == 1


def test_final_provenance_cap_includes_persistence_metadata(cdsn, tmp_path):
    cfg = settings(tmp_path)
    item = snapshot(captured_at=store.utcnow())
    cap = prepare_capture(item, cfg)
    base = dict(cap.facts_provenance)
    base['padding'] = ''
    base['padding'] = 'x' * (4090-len(json.dumps(base,ensure_ascii=False).encode()))
    assert len(json.dumps(base,ensure_ascii=False).encode()) <= 4096
    cap = cap.model_copy(update={'facts_provenance':base})
    result = capture_hook(cdsn, cap, cfg.shadow_config(), captured_at=item.captured_at)
    assert result.state == 'dropped' and result.skip_reason == 'provenance_too_large'
    with psycopg.connect(cdsn) as conn:
        assert conn.execute('SELECT consumed_rows FROM classifier_hook_capture_budget').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM classifier_observations').fetchone()[0] == 0


def test_lost_commit_acknowledgement_stays_charged_and_is_not_retried(cdsn, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from bridge.classifier import hook_store
    from bridge.classifier.hook_store import CaptureCommitUncertain
    cfg = settings(tmp_path)
    item = snapshot(captured_at=store.utcnow())
    cap = prepare_capture(item, cfg)
    connect = psycopg.connect
    class LostAck:
        def __init__(self, *a, **kw): self.conn = connect(*a, **kw)
        def __enter__(self): self.conn.__enter__(); return self
        def __exit__(self, *args): return self.conn.__exit__(*args)
        def execute(self, *a, **kw): return self.conn.execute(*a, **kw)
        @contextmanager
        def transaction(self):
            with self.conn.transaction(): yield
            raise psycopg.OperationalError('commit acknowledgement lost')
    with monkeypatch.context() as patch:
        patch.setattr(hook_store.psycopg, 'connect', LostAck)
        with pytest.raises(CaptureCommitUncertain):
            capture_hook(cdsn, cap, cfg.shadow_config(), captured_at=item.captured_at)
    with connect(cdsn) as conn:
        assert conn.execute('SELECT consumed_rows FROM classifier_hook_capture_budget').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM classifier_observations').fetchone()[0] == 1
    # A new process/profile cannot make the same arrival into an uncharged second write.
    assert capture_hook(cdsn, cap, cfg.shadow_config(), captured_at=item.captured_at).state == 'duplicate'
    with connect(cdsn) as conn:
        assert conn.execute('SELECT consumed_rows FROM classifier_hook_capture_budget').fetchone()[0] == 1
