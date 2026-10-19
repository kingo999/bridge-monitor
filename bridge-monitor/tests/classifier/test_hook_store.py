"""Automatic losses never write rejected payloads or bypass shared accounting."""

from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from bridge.classifier import store
from tests.classifier.conftest import table_counts
from tests.classifier.test_hook_capture import NOW, settings, snapshot


def capture(cdsn, tmp_path, **changes):
    from bridge.classifier.hook_capture import prepare_capture
    from bridge.classifier.hook_store import capture_hook
    cfg = settings(tmp_path)
    item = snapshot(**changes)
    return capture_hook(cdsn, prepare_capture(item, cfg), cfg.shadow_config(),
                        captured_at=item.captured_at, now=NOW)


def counts(dsn):
    with psycopg.connect(dsn) as conn:
        return tuple(conn.execute('SELECT (SELECT count(*) FROM classifier_observations),'
            '(SELECT count(*) FROM content_objects WHERE content_id LIKE \'classifier:%\'),'
            '(SELECT consumed_rows FROM classifier_hook_capture_budget),'
            '(SELECT consumed_bytes FROM classifier_hook_capture_budget)').fetchone())


def reset_rate(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE classifier_hook_capture_budget SET last_persisted_at=now()-interval '3 seconds'")


def test_hook_deduplicates_without_charge_and_keeps_hook_time(cdsn, tmp_path):
    before = table_counts(cdsn)
    first = capture(cdsn, tmp_path)
    assert first.state == 'captured'
    charged = counts(cdsn)
    assert capture(cdsn, tmp_path).state == 'duplicate'
    assert counts(cdsn) == charged and charged[2] == 1
    with psycopg.connect(cdsn) as conn:
        row = conn.execute('SELECT captured_at,facts_captured_at,input_bytes FROM classifier_observations').fetchone()
        assert row[:2] == (NOW, NOW) and row[2] == charged[3]
    assert table_counts(cdsn) == before


@pytest.mark.parametrize('reason', ['rate_limited', 'database_queue_full', 'capture_budget_exhausted',
                                   'input_too_large', 'expired'])
def test_drops_add_no_observations_or_content(cdsn, tmp_path, reason):
    if reason == 'rate_limited':
        capture(cdsn, tmp_path)
    elif reason == 'database_queue_full':
        cfg = settings(tmp_path).shadow_config()
        cfg.max_queue = 1
        store.capture(cdsn, __import__('bridge.classifier.hook_capture', fromlist=['prepare_capture']).prepare_capture(
            snapshot(), settings(tmp_path)), cfg, now=NOW)
        # Manual row already fills the queue; hook's default queue is eight.
        with psycopg.connect(cdsn) as conn:
            conn.execute("UPDATE classifier_hook_capture_budget SET consumed_rows=0")
    elif reason == 'capture_budget_exhausted':
        with psycopg.connect(cdsn) as conn:
            conn.execute('UPDATE classifier_hook_capture_budget SET consumed_rows=256')
    from bridge.classifier.hook_capture import prepare_capture
    from bridge.classifier.hook_store import capture_hook
    cfg = settings(tmp_path).shadow_config()
    item = snapshot(work_item_id='new-work')
    cap = prepare_capture(item, settings(tmp_path))
    now = NOW
    if reason == 'database_queue_full': cfg.max_queue = 1
    if reason == 'input_too_large': cfg.max_input_bytes = 256
    if reason == 'expired': now += timedelta(seconds=601)
    before = counts(cdsn)
    result = capture_hook(cdsn, cap, cfg, captured_at=NOW, now=now)
    assert result.state == 'dropped' and result.skip_reason == reason
    assert counts(cdsn) == before


def test_byte_budget_and_last_slot_are_shared_and_atomic(cdsn, tmp_path):
    from bridge.classifier.hook_capture import prepare_capture
    from bridge.classifier.hook_store import capture_hook
    cfg = settings(tmp_path)
    with psycopg.connect(cdsn) as conn:
        conn.execute('UPDATE classifier_hook_capture_budget SET consumed_rows=255')
    caps = [prepare_capture(snapshot(work_item_id=f'w-{i}'), cfg) for i in range(2)]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda cap: capture_hook(cdsn, cap, cfg.shadow_config(),
            captured_at=NOW, now=NOW), caps))
    assert sum(r.state == 'captured' for r in results) == 1
    assert counts(cdsn)[2] == 256


def test_byte_exhaustion_and_failed_insert_leave_content_and_budget_atomic(cdsn, tmp_path, monkeypatch):
    from bridge.classifier.hook_store import capture_hook
    from bridge.classifier.hook_capture import prepare_capture
    cfg = settings(tmp_path)
    cap = prepare_capture(snapshot(), cfg)
    with psycopg.connect(cdsn) as conn:
        conn.execute('UPDATE classifier_hook_capture_budget SET consumed_bytes=4194303')
    before = counts(cdsn)
    assert capture_hook(cdsn, cap, cfg.shadow_config(), captured_at=NOW, now=NOW).skip_reason == 'capture_budget_exhausted'
    assert counts(cdsn) == before
    with psycopg.connect(cdsn) as conn:
        conn.execute('UPDATE classifier_hook_capture_budget SET consumed_bytes=0')
    before = counts(cdsn)
    monkeypatch.setattr(store, 'insert_capture', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError):
        capture_hook(cdsn, cap, cfg.shadow_config(), captured_at=NOW, now=NOW)
    assert counts(cdsn) == before


def test_capture_lock_wait_is_bounded_and_creates_nothing(cdsn, tmp_path):
    from bridge.classifier.hook_store import CaptureDatabaseError, capture_hook
    from bridge.classifier.hook_capture import prepare_capture
    cfg = settings(tmp_path)
    before = counts(cdsn)
    with psycopg.connect(cdsn) as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(%s)', (store.CAPTURE_LOCK,))
        with pytest.raises(CaptureDatabaseError) as error:
            capture_hook(cdsn, prepare_capture(snapshot(), cfg), cfg.shadow_config(), captured_at=NOW, now=NOW)
        assert isinstance(error.value.__cause__, psycopg.errors.LockNotAvailable)
    assert counts(cdsn) == before
