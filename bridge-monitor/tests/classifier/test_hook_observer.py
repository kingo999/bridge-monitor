"""A blocked or failing optional sink cannot hold the request thread or shutdown."""

import threading
import time
import psycopg
import pytest
from dataclasses import replace
from datetime import timedelta

from tests.classifier.test_hook_capture import NOW, settings, snapshot


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def test_blocked_worker_has_eight_pending_and_close_is_bounded(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    from bridge.classifier.store import CaptureResult
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return CaptureResult('captured')
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=blocked, utcnow=lambda: NOW)
    observer.offer(snapshot())
    assert entered.wait(2)
    start = time.monotonic()
    for i in range(30): observer.offer(snapshot(work_item_id=f'w-{i}'))
    assert time.monotonic() - start < .1
    assert observer.pending == 8 and observer.counters['memory_queue_full'] == 22
    assert observer.thread.daemon
    start = time.monotonic()
    observer.close()
    assert time.monotonic() - start < .4
    assert observer.pending == 0
    release.set()
    observer.thread.join(2)
    assert not observer.thread.is_alive()


def test_offer_drops_on_mutex_contention_instead_of_waiting(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    observer = RouteCaptureObserver('unused', settings(tmp_path))
    try:
        observer._guard.acquire()
        started = time.monotonic()
        observer.offer(snapshot())
        assert time.monotonic() - started < .05
        assert observer.counters['submission_contention'] == 1
    finally:
        observer._guard.release()
        observer.close()


def test_error_latches_sink_without_retry_or_replacement(tmp_path, capsys):
    from bridge.classifier.observer import RouteCaptureObserver
    calls = []
    def broken(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('secret body must not be printed')
    observer = RouteCaptureObserver('secret-dsn', settings(tmp_path), persist=broken, utcnow=lambda: NOW)
    try:
        observer.offer(snapshot())
        wait_for(lambda: observer.disabled)
        for _ in range(10): observer.offer(snapshot())
        assert calls == [1] and observer.counters['persistence_uncertain'] == 1
        assert observer.pending == 0
    finally: observer.close()
    assert 'secret' not in capsys.readouterr().err


def test_expired_and_invalid_items_never_reach_persistence(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    calls = []
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=lambda *a, **kw: calls.append(1),
                                    utcnow=lambda: NOW + timedelta(seconds=601))
    try:
        observer.offer(snapshot())
        observer.offer(snapshot(case_task_id='wrong'))
        wait_for(lambda: observer.counters['expired'] == 1)
        assert not calls and observer.counters['binding_skipped'] == 1
    finally: observer.close()


def test_cooldown_drops_without_another_database_attempt(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    from bridge.classifier.store import CaptureResult
    calls = []
    def persist(*args, **kwargs):
        calls.append(args[1])
        return CaptureResult('captured')
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=persist, utcnow=lambda: NOW,
                                    monotonic=lambda: 10)
    try:
        observer.offer(snapshot())
        wait_for(lambda: len(calls) == 1)
        observer.offer(snapshot(work_item_id='w2'))
        wait_for(lambda: observer.counters['rate_limited'] == 1)
        assert len(calls) == 1
    finally: observer.close()


def test_shutdown_does_not_wait_for_a_contended_offer_mutex(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    observer = RouteCaptureObserver('unused', settings(tmp_path))
    done = threading.Event()
    observer._guard.acquire()
    closer = threading.Thread(target=lambda: (observer.close(), done.set()), daemon=True)
    try:
        closer.start()
        assert done.wait(.5)
    finally:
        observer._guard.release()
        closer.join(2)
        observer.thread.join(2)


def test_malformed_unicode_item_is_dropped_without_disabling_later_capture(tmp_path):
    from bridge.classifier.observer import RouteCaptureObserver
    from bridge.classifier.store import CaptureResult
    saved = []
    def persist(*args, **kwargs):
        saved.append(args[1])
        return CaptureResult('captured')
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=persist, utcnow=lambda: NOW)
    try:
        observer.offer(snapshot(prompt='bad\ud800', case_prompt='bad\ud800'))
        observer.offer(snapshot())
        wait_for(lambda: len(saved) == 1)
        assert not observer.disabled and observer.counters['binding_skipped'] == 1
    finally: observer.close()


@pytest.mark.parametrize('error', [psycopg.errors.LockNotAvailable(), psycopg.errors.QueryCanceled(),
                                  psycopg.OperationalError('connect failure')])
def test_known_database_failure_latches_without_claiming_an_uncertain_commit(tmp_path, error):
    from bridge.classifier.observer import RouteCaptureObserver
    def broken(*args, **kwargs): raise error
    observer = RouteCaptureObserver('unused', settings(tmp_path), persist=broken, utcnow=lambda: NOW)
    try:
        observer.offer(snapshot())
        wait_for(lambda: observer.disabled)
        assert observer.counters['database_error'] == 1
        assert observer.counters['persistence_uncertain'] == 0
    finally: observer.close()
