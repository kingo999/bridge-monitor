"""One optional daemon sink. The offer path performs no I/O and never waits for a mutex."""

import sys
import threading
import time
from collections import deque
import psycopg

from bridge.classifier import store
from bridge.classifier.hook_capture import prepare_capture
from bridge.classifier.hook_config import HookSettings
from bridge.classifier.hook_store import CaptureDatabaseError, capture_hook
from contracts.route_capture import RouteCapture

COUNTERS = ('offers', 'accepted', 'memory_queue_full', 'submission_contention',
    'binding_skipped', 'input_too_large', 'expired', 'rate_limited', 'database_queue_full',
    'capture_budget_exhausted', 'solution_already_available', 'captured', 'duplicate',
    'persistence_uncertain', 'database_error', 'provenance_too_large',
    'disabled_offers', 'discarded', 'worker_failed')


class RouteCaptureObserver:
    def __init__(self, dsn: str, settings: HookSettings, *, persist=capture_hook,
                 utcnow=store.utcnow, monotonic=time.monotonic):
        self._dsn, self._settings = dsn, settings
        self._cfg = settings.shadow_config()
        self._persist, self._utcnow, self._monotonic = persist, utcnow, monotonic
        self._guard = threading.Lock()
        self._queue = deque()
        self._stop = threading.Event()
        self.disabled = False
        # Approximate process-local coverage counters, not a durable audit denominator.
        self.counters = dict.fromkeys(COUNTERS, 0)
        self._last_attempt = float('-inf')
        self._last_diagnostic = float('-inf')
        self.thread = threading.Thread(target=self._run, name='route-capture', daemon=True)
        self.thread.start()

    @property
    def normal_traffic_enabled(self):
        return self._settings.normal_traffic_enabled

    @property
    def pending(self):
        return len(self._queue)

    def offer(self, snapshot: RouteCapture) -> None:
        self.counters['offers'] += 1
        if self.disabled:
            self.counters['disabled_offers'] += 1
            return
        if not snapshot.acceptable():
            self.counters['binding_skipped'] += 1
            return
        if not self._guard.acquire(blocking=False):
            self.counters['submission_contention'] += 1
            return
        try:
            if self.disabled:
                self.counters['disabled_offers'] += 1
            elif len(self._queue) >= 8:
                self.counters['memory_queue_full'] += 1
            else:
                self._queue.append(snapshot)
                self.counters['accepted'] += 1
        finally:
            self._guard.release()
        # No Event.set/Condition notification here: those can wait for a lock.

    def _disable(self):
        self.disabled = True
        with self._guard:
            self.counters['discarded'] += len(self._queue)
            self._queue.clear()

    def _diagnostic(self, reason):
        now = self._monotonic()
        if now - self._last_diagnostic >= 30:
            self._last_diagnostic = now
            try:
                print(f'route-capture-v1: {reason}', file=sys.stderr)
            except Exception:
                pass

    def _run(self):
        try:
            while not self._stop.is_set() and not self.disabled:
                with self._guard:
                    item = self._queue.popleft() if self._queue else None
                if item is None:
                    self._stop.wait(.05)
                    continue
                now = self._utcnow()
                if (now - item.captured_at).total_seconds() > min(600, self._cfg.max_queue_age_seconds):
                    self.counters['expired'] += 1
                    continue
                try:
                    cap = prepare_capture(item, self._settings)
                    if cap is None:
                        self.counters['binding_skipped'] += 1
                        continue
                    if cap.input.input_bytes() > min(24576, self._cfg.max_input_bytes):
                        self.counters['input_too_large'] += 1
                        continue
                except (ValueError, TypeError):
                    self.counters['binding_skipped'] += 1
                    continue
                tick = self._monotonic()
                if tick - self._last_attempt < 2:
                    self.counters['rate_limited'] += 1
                    continue
                if self.disabled:
                    break
                self._last_attempt = tick
                try:
                    result = self._persist(self._dsn, cap, self._cfg, captured_at=item.captured_at, now=now)
                except Exception as exc:
                    # Includes an ambiguous commit: never retry and never print exception text.
                    reason = ('database_error' if isinstance(exc, (CaptureDatabaseError, psycopg.Error))
                              else 'persistence_uncertain')
                    self.counters[reason] += 1
                    self._disable()
                    self._diagnostic(reason + '_sink_disabled')
                    break
                reason = result.skip_reason if result.state == 'dropped' else result.state
                if reason in self.counters:
                    self.counters[reason] += 1
                if reason == 'capture_budget_exhausted':
                    self._disable()
                    self._diagnostic('capture_budget_exhausted')
        except Exception:
            self.counters['worker_failed'] += 1
            self._disable()
            self._diagnostic('worker_failed_sink_disabled')
        finally:
            self._disable()

    def close(self) -> None:
        self.disabled = True
        if self._guard.acquire(blocking=False):
            try:
                self.counters['discarded'] += len(self._queue)
                self._queue.clear()
            finally:
                self._guard.release()
        # If an offer held the mutex, the daemon's finally block discards the queue.
        self._stop.set()
        self.thread.join(timeout=.25)
