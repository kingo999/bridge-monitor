"""Oversized arrivals remain visible without retained payloads or inference."""
import threading
from dataclasses import replace
from datetime import timedelta

import psycopg
import pytest

from tests.classifier.test_hook_capture import NOW, settings, snapshot
from tests.classifier.test_hook_observer import wait_for


def test_oversize_offer_queues_only_bounded_metadata(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    from bridge.classifier.store import CaptureResult
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set(); release.wait(5)
        return CaptureResult('captured')
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=blocked, utcnow=lambda: NOW)
    try:
        observer.offer(snapshot())
        assert entered.wait(2)
        text = 'x' * 49033
        observer.offer(snapshot(prompt=text, case_prompt=text, work_item_id='oversize'))
        assert observer.pending == 1
        queued = observer._queue[0]
        assert not hasattr(queued, 'prompt') and not hasattr(queued, 'source')
        assert queued.size_kind == 'character_lower_bound' and queued.input_size == 49060
    finally:
        observer.close(); release.set(); observer.thread.join(2)


def test_invalid_oversize_binding_still_cannot_enter_queue(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    observer = RouteCaptureObserver('unused', settings(tmp_path), utcnow=lambda: NOW)
    try:
        text = 'x' * 49033
        observer.offer(snapshot(prompt=text, case_prompt=text, scope_approved=False))
        assert observer.pending == 0 and observer.counters['binding_skipped'] == 1
    finally: observer.close()


@pytest.mark.parametrize('bad_id',['bad\x00id','bad\ud800id'],ids=['nul','surrogate'])
def test_bad_metadata_cannot_disable_capture_sink(tmp_path,bad_id):
    from bridge.classifier.exclusions import project_exclusion
    text='x'*49033
    assert project_exclusion(snapshot(prompt=text,case_prompt=text,work_item_id=bad_id)) is None
    assert snapshot(work_item_id=bad_id).acceptable() is False


def test_disabled_normal_ingress_does_not_queue_oversize_metadata(tmp_path):
    from contracts.route_capture import BridgeCapture
    from bridge.classifier.observer import RouteCaptureObserver
    observer=RouteCaptureObserver('unused',settings(tmp_path),utcnow=lambda:NOW)
    try:
        observer.offer(BridgeCapture('t','w','x'*49033,'route_work','c','d','sonnet',NOW))
        assert observer.pending==0 and observer.counters['binding_skipped']==1
    finally: observer.close()


@pytest.fixture
def exclusion_env(cdsn, tmp_path):
    from bridge.classifier import store
    cfg = settings(tmp_path)
    item = snapshot(captured_at=store.utcnow())
    return cdsn, cfg, item


def save_oversize(env, **changes):
    from bridge.classifier.exclusions import project_exclusion, persist_exclusion
    dsn, cfg, item = env
    text = 'x' * 49033
    item = replace(item, prompt=text, case_prompt=text, **changes)
    meta = project_exclusion(item)
    assert meta is not None
    return persist_exclusion(dsn, meta, cfg, now=item.captured_at)


def test_exclusion_is_durable_append_only_and_charged_once(exclusion_env):
    from bridge.classifier.runner import ShadowRunner
    from tests.classifier.fakes import FakeOllama
    dsn, cfg, item = exclusion_env
    first = save_oversize(exclusion_env)
    assert first.state == 'excluded' and first.skip_reason == 'input_too_large'
    assert save_oversize(exclusion_env).state == 'duplicate'
    with psycopg.connect(dsn) as c:
        row = c.execute('SELECT reason,input_size,size_kind,metadata_bytes FROM classifier_capture_exclusions').fetchone()
        assert row[:3] == ('input_too_large', 49060, 'character_lower_bound')
        assert c.execute('SELECT consumed_rows,consumed_bytes FROM classifier_hook_capture_budget').fetchone() == (1,row[3])
        assert c.execute('SELECT count(*) FROM classifier_observations').fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM content_objects WHERE content_id LIKE 'classifier:%'").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.RaiseException), c.transaction():
            c.execute("UPDATE classifier_capture_exclusions SET reason='input_too_large'")
    fake = FakeOllama()
    with ShadowRunner(dsn, cfg.shadow_config(), fake.classifier()) as runner:
        assert runner.process_one().status == 'idle'
    assert fake.chat_calls == 0


@pytest.mark.parametrize('value', ["é"*13000, '"'*13000], ids=['utf8','json_escape'])
def test_exact_serialized_byte_overflow_persists_exclusion(exclusion_env, value):
    from bridge.classifier.hook_capture import prepare_capture
    from bridge.classifier.hook_store import capture_hook
    dsn, cfg, item = exclusion_env
    item = replace(item, prompt=value, case_prompt=value)
    cap = prepare_capture(item, cfg)
    assert cap is not None and cap.input.input_bytes() > 24576
    result = capture_hook(dsn, cap, cfg.shadow_config(), captured_at=item.captured_at, now=item.captured_at)
    assert result.state == 'excluded'
    with psycopg.connect(dsn) as c:
        row = c.execute('SELECT input_size,size_kind FROM classifier_capture_exclusions').fetchone()
        assert row == (cap.input.input_bytes(), 'serialized_utf8_exact')
        assert c.execute('SELECT count(*) FROM classifier_observations').fetchone()[0] == 0


@pytest.mark.parametrize('rows,used', [(256,0), (0,4194303)])
def test_exclusion_respects_existing_lifetime_quota(exclusion_env, rows, used):
    dsn, _, _ = exclusion_env
    with psycopg.connect(dsn) as c:
        c.execute('UPDATE classifier_hook_capture_budget SET consumed_rows=%s,consumed_bytes=%s',(rows,used))
    result = save_oversize(exclusion_env)
    assert result.skip_reason == 'capture_budget_exhausted'
    with psycopg.connect(dsn) as c:
        assert c.execute('SELECT count(*) FROM classifier_capture_exclusions').fetchone()[0] == 0
        assert c.execute('SELECT consumed_rows,consumed_bytes FROM classifier_hook_capture_budget').fetchone() == (rows,used)


def test_concurrent_duplicate_exclusion_is_single_charged_row(exclusion_env):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: save_oversize(exclusion_env), range(2)))
    assert sorted(r.state for r in results) == ['duplicate','excluded']
    with psycopg.connect(exclusion_env[0]) as c:
        assert c.execute('SELECT count(*) FROM classifier_capture_exclusions').fetchone()[0] == 1
        assert c.execute('SELECT consumed_rows FROM classifier_hook_capture_budget').fetchone()[0] == 1


def test_dashboard_separates_exclusions_from_classified_quality(exclusion_env):
    from service.queries.classifier import query_classifier_cohort, ClassifierCohortFilter
    save_oversize(exclusion_env)
    result = query_classifier_cohort(exclusion_env[0], ClassifierCohortFilter())
    assert result['counts']['submitted'] == 0
    assert result['capture_exclusions']['total'] == 1
    assert result['capture_exclusions']['reasons'] == {'input_too_large':1}
