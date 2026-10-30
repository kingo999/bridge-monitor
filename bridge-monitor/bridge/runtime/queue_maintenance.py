"""Automatic maintenance for abandoned, never-launched Bridge queue entries."""
from datetime import datetime, timezone
import logging
import threading

from bridge.runtime.codex_claude import reconcile_expired_codex_claude_queue


class QueueExpiryMonitor:
    """Runs in the existing Monitor service; never stops or replays inference."""

    def __init__(self, dsn, *, interval_seconds=5):
        if interval_seconds <= 0:
            raise ValueError('queue monitor interval must be positive')
        self._dsn = dsn
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._stats = dict(last_success_at=None, last_error_type=None,
                           failed_sweeps=0, expired_total=0, released_total=0)

    def start(self):
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run,
                name='bridge-queue-expiry', daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                result = reconcile_expired_codex_claude_queue(self._dsn)
                with self._lock:
                    self._stats.update(last_success_at=datetime.now(timezone.utc).isoformat(),
                                       last_error_type=None)
                    self._stats['expired_total'] += len(result['expired'])
                    self._stats['released_total'] += len(result['released'])
            except Exception as exc:
                # No DSN, query parameters or private source in logs/status.
                with self._lock:
                    self._stats['last_error_type'] = type(exc).__name__
                    self._stats['failed_sweeps'] += 1
                logging.getLogger(__name__).warning('Queue expiry sweep failed (%s); retrying', type(exc).__name__)
            self._stop.wait(self._interval)

    def snapshot(self):
        with self._lock:
            return {**self._stats, 'interval_seconds': self._interval,
                    'running': self._thread is not None and self._thread.is_alive()}

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=12)
