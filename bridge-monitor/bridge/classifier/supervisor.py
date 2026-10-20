"""Single-process automatic recovery for the advisory classifier only.

Task Scheduler restarts this process after crashes. The OS lease survives neither
crashes nor reboot; a persistent safety halt survives both. No dispatcher is imported.
"""
import json
import logging
import os
from pathlib import Path
import threading
import time
from datetime import datetime, timezone

import psycopg
from psycopg.conninfo import conninfo_to_dict

from bridge.classifier import winproc
from bridge.classifier.client import OllamaClassifier
from bridge.classifier.lifecycle import RuntimeController
from bridge.classifier.runner import RunnerBusy, ShadowRunner

LOG = logging.getLogger('classifier-auto')


class SafetyHalt(RuntimeError):
    """Requires operator review; scheduler retries must never clear this."""


class RuntimeUnavailable(RuntimeError):
    """The owned runtime may be retried with backoff."""


class SingleInstance:
    """Kernel-held file lease, shared across Windows sessions and released on exit."""
    def __init__(self, path: Path):
        self.path, self.handle, self.acquired = path, None, False

    def __enter__(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            self.acquired = True
        except OSError:
            self.handle.close()
            self.handle = None
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        self.acquired = False


def atomic_json(path: Path, body: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(body, indent=2, default=str) + '\n', encoding='utf-8')
    # Windows readers/scanners can briefly deny rename/delete sharing. Retry
    # only the rename of this already-closed file, never the classification or
    # database operation. Persistent permissions and other IO failures escape.
    delays = (.01, .02, .04, .08, .16)
    first_error = None
    for attempt in range(len(delays) + 1):
        try:
            os.replace(temporary, path)
        except OSError as exc:
            code = getattr(exc, 'winerror', None)
            if os.name != 'nt' or code not in (5, 32, 33) or attempt == len(delays):
                LOG.error('state_file_replace_failed winerror=%s errno=%s attempts=%s',
                          code, exc.errno, attempt + 1)
                raise
            if first_error is None:
                first_error = code
            time.sleep(delays[attempt])
        else:
            if attempt:
                LOG.warning('state_file_replace_recovered winerror=%s retries=%s',
                            first_error, attempt)
            return


def validate_dsn(dsn: str, expected_database: str):
    values = conninfo_to_dict(dsn)
    if (values.get('host') != '127.0.0.1'
            or values.get('hostaddr', '127.0.0.1') != '127.0.0.1'
            or values.get('dbname') != expected_database):
        raise SafetyHalt('database_identity_mismatch')


def make_client(cfg):
    return OllamaClassifier(endpoint=cfg.endpoint, model=cfg.model,
        expected_digest=cfg.model_digest, profile=cfg.profile, keep_alive=cfg.keep_alive,
        connect_timeout=cfg.connect_timeout_seconds, request_timeout=cfg.request_timeout_seconds,
        max_response_bytes=cfg.max_response_bytes)


class SafetyClient:
    """Save unsafe residency before the runner attempts to persist its response."""
    def __init__(self, client, latch):
        self.client, self.latch = client, latch

    def __getattr__(self, name):
        return getattr(self.client, name)

    def residency(self):
        result = self.client.residency()
        if result.state in ('cpu_only', 'partial_offload'):
            self.latch('gpu_residency_' + result.state)
        return result


def ensure_runtime(runtime):
    if runtime.preflight():
        raise SafetyHalt('runtime_or_manifest_digest_mismatch')
    started = runtime.start(warm=False)
    if started.get('exit') != 0:
        if started.get('reason') in ('server_exited_during_startup', 'startup_timeout'):
            raise RuntimeUnavailable('runtime_startup_unavailable')
        raise SafetyHalt('runtime_start_refused')
    report = runtime.status(probe=True)
    if report['overall'] == 'ready':
        return
    model_state = report.get('model', {}).get('state')
    if report['overall'] == 'unowned_listener' or model_state in (
            'model_digest_mismatch', 'model_not_installed'):
        raise SafetyHalt('runtime_identity_mismatch')
    residency = report.get('gpu', {}).get('residency', {}).get('state')
    if residency in ('cpu_only', 'partial_offload') or report.get('gpu', {}).get('log', {}).get('state') == 'unexpected_device':
        raise SafetyHalt('gpu_residency_not_safe')
    raise RuntimeUnavailable('runtime_not_ready')


def serve(cfg, dsn: str, state: Path, stop: threading.Event) -> int:
    """Own both locks and run classifications in this process, leaving no child runner."""
    with SingleInstance(state / 'supervisor.lock') as lease:
        if not lease.acquired:
            return 0
        identity = winproc.identify(os.getpid())
        base = {'pid': os.getpid(), 'image_path': identity.image_path,
                'created_ticks': identity.created_ticks,
                'application_name': f'gemma-classifier-auto-{os.getpid()}',
                'started_at': datetime.now(timezone.utc).isoformat()}
        last_write = 0.0
        last_status = None
        def publish(status, *, force=False, **details):
            nonlocal last_write, last_status
            if force or status != last_status or time.monotonic() - last_write >= 30:
                atomic_json(state / 'service.json', {**base, 'status': status,
                    'checked_at': datetime.now(timezone.utc).isoformat(), **details})
                last_write = time.monotonic()
                last_status = status
        if (state / 'halt.json').exists() or (state / 'stop.requested').exists():
            publish('paused', force=True)
            return 0
        runner = client = runtime = None
        ready, delay = False, 5
        safety_reason = None
        def latch(reason):
            nonlocal safety_reason
            safety_reason = reason
            atomic_json(state / 'halt.json', {'reason': reason, 'at': datetime.now(timezone.utc).isoformat()})
        try:
            runtime = RuntimeController(cfg.gemma_runtime)
            while (not stop.is_set() and not (state / 'stop.requested').exists()
                   and not (state / 'halt.json').exists()):
                try:
                    if runner is None:
                        client = SafetyClient(make_client(cfg.classifier_shadow), latch)
                        runner = ShadowRunner(dsn, cfg.classifier_shadow, client)
                        try:
                            recovered = runner.start()
                            LOG.info('runner_started recovered_uncertain=%s', recovered)
                        except RunnerBusy:
                            runner.close(); client.close(); runner = client = None
                            publish('waiting_for_existing_runner')
                            stop.wait(5)
                            continue
                    # A lost PostgreSQL session also loses its advisory lock. Never
                    # continue using a stale runner after database recovery.
                    runner._lock_conn.execute('SELECT 1')
                    if not ready:
                        ensure_runtime(runtime)
                        ready = True
                    # Warmup may outlast database availability or a pause request.
                    if (stop.is_set() or (state / 'stop.requested').exists()
                            or (state / 'halt.json').exists()):
                        break
                    runner._lock_conn.execute('SELECT 1')
                    result = runner.process_one(stop)
                    delay = 5
                    publish('running', force=result.status == 'processed',
                            last_result=result.status, observation_id=result.observation_id)
                    if safety_reason or runner.halted:
                        raise SafetyHalt(safety_reason or runner.halted)
                    if result.status == 'unavailable':
                        ready = False
                        if result.reason in ('model_digest_mismatch', 'model_not_installed'):
                            raise SafetyHalt('runtime_identity_mismatch')
                        raise RuntimeUnavailable('runtime_unavailable')
                    if result.status != 'processed':
                        stop.wait(2)
                except (psycopg.Error, RuntimeUnavailable) as exc:
                    if safety_reason:
                        raise SafetyHalt(safety_reason)
                    # Recovery never sends a dispatching observation again: start()
                    # marks interrupted sends uncertain before claiming new rows.
                    if runner is not None: runner.close()
                    if client is not None: client.close()
                    runner = client = None
                    ready = False
                    LOG.warning('retry type=%s delay=%s', type(exc).__name__, delay)
                    publish('retrying', force=True, error_type=type(exc).__name__, retry_seconds=delay)
                    stop.wait(delay)
                    delay = min(delay * 2, 60)
            publish('stopped', force=True)
            return 0
        except SafetyHalt as exc:
            reason = str(exc)  # only fixed internal reason strings, never DB exceptions
            latch(reason)
            publish('halted', force=True, reason=reason)
            LOG.error('safety_halt reason=%s', reason)
            return 0
        except Exception as exc:
            publish('crashed', force=True, error_type=type(exc).__name__)
            LOG.error('crashed type=%s', type(exc).__name__)
            return 1
        finally:
            if runner is not None: runner.close()
            if client is not None: client.close()
            if runtime is not None: runtime.http.close()
