"""Recovery controls use no model and never select the operational database."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psycopg
import pytest

from bridge.classifier import supervisor as service
from bridge.classifier.config import ClassifierConfig
from bridge.classifier.runner import ProcessResult, RunnerBusy

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows automatic task')


@pytest.fixture
def harness(tmp_path, monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(stop, 'wait', lambda seconds: stop.is_set())
    events = []
    runtime = SimpleNamespace(http=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(service, 'RuntimeController', lambda cfg: runtime)
    monkeypatch.setattr(service, 'ensure_runtime', lambda runtime: events.append('ready'))
    class Runner:
        halted = None
        def __init__(self, *args):
            self.classifier = args[2]
            self._lock_conn = SimpleNamespace(execute=lambda query: events.append('lock_checked'))
        def start(self): events.append('lock_acquired')
        def process_one(self, cancel):
            events.append('process'); stop.set(); return ProcessResult('idle')
        def close(self): events.append('lock_closed')
    client = SimpleNamespace(close=lambda: events.append('client_closed'))
    monkeypatch.setattr(service, 'make_client', lambda cfg: client)
    monkeypatch.setattr(service, 'ShadowRunner', Runner)
    cfg = ClassifierConfig.model_validate({'classifier_shadow': {'enabled': True}})
    return SimpleNamespace(state=tmp_path, stop=stop, events=events, Runner=Runner,
                           run=lambda: service.serve(cfg, 'fake-dsn', tmp_path, stop))


def test_real_process_lease_excludes_duplicate_and_is_released_after_crash(tmp_path):
    lock = tmp_path / 'supervisor.lock'
    ready = tmp_path / 'ready'
    code = ('from pathlib import Path; import sys,time; '
            'from bridge.classifier.supervisor import SingleInstance; '
            'lease=SingleInstance(Path(sys.argv[1])); '
            'assert lease.__enter__().acquired; Path(sys.argv[2]).touch(); time.sleep(60)')
    child = subprocess.Popen([sys._base_executable, '-c', code, str(lock), str(ready)],
                             cwd=str(Path(__file__).resolve().parents[2]),
                             env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)},
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert child.poll() is None, child.stderr.read().decode()
            assert time.monotonic() < deadline
            time.sleep(.02)
        with service.SingleInstance(lock) as duplicate:
            assert not duplicate.acquired
        child.kill(); child.wait(timeout=10)
        with service.SingleInstance(lock) as recovered:
            assert recovered.acquired
    finally:
        if child.poll() is None: child.kill(); child.wait(timeout=10)
        child.stderr.close()


def test_duplicate_does_not_replace_owner_receipt(harness):
    owner = harness.state / 'service.json'
    owner.write_text('{"owner":"original"}')
    with service.SingleInstance(harness.state / 'supervisor.lock'):
        assert harness.run() == 0
    assert json.loads(owner.read_text()) == {'owner': 'original'}
    assert harness.events == []


@pytest.mark.parametrize('marker', ['stop.requested', 'halt.json'])
def test_persistent_stop_and_safety_halt_prevent_any_start(harness, marker):
    (harness.state / marker).write_text('{}')
    assert harness.run() == 0
    assert harness.events == []


def test_database_lock_is_acquired_and_checked_before_runtime_or_classification(harness):
    assert harness.run() == 0
    assert harness.events[:5] == ['lock_acquired', 'lock_checked', 'ready', 'lock_checked', 'process']
    assert 'lock_closed' in harness.events and 'client_closed' in harness.events


def test_existing_runner_is_left_alone_without_starting_runtime(harness, monkeypatch):
    def busy(self):
        harness.stop.set(); raise RunnerBusy('another runner')
    monkeypatch.setattr(harness.Runner, 'start', busy)
    assert harness.run() == 0
    assert 'ready' not in harness.events and 'process' not in harness.events


def test_lost_lock_connection_is_closed_and_reacquired_before_processing(harness, monkeypatch):
    original = harness.Runner.__init__
    count = []
    def init(self, *args):
        original(self, *args); count.append(1)
        if len(count) == 1:
            def lost(query): raise psycopg.OperationalError('sensitive-dsn')
            self._lock_conn = SimpleNamespace(execute=lost)
    monkeypatch.setattr(harness.Runner, '__init__', init)
    assert harness.run() == 0
    assert len(count) == 2
    assert harness.events.count('process') == 1
    assert harness.events.index('lock_closed') < harness.events.index('ready')
    assert 'sensitive-dsn' not in (harness.state / 'service.json').read_text()


def test_dead_runtime_is_recovered_before_next_queue_pass(harness, monkeypatch):
    calls = []
    def process(self, cancel):
        calls.append(1)
        if len(calls) == 1: return ProcessResult('unavailable', reason='endpoint_unavailable')
        harness.stop.set(); return ProcessResult('idle')
    monkeypatch.setattr(harness.Runner, 'process_one', process)
    assert harness.run() == 0
    assert len(calls) == 2 and harness.events.count('ready') == 2


def test_status_file_retry_does_not_repeat_classification(harness, monkeypatch):
    import ctypes
    original = service.os.replace
    attempts = []
    def replace(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            raise ctypes.WinError(5)
        return original(source, destination)
    monkeypatch.setattr(service.os, 'replace', replace)
    assert harness.run() == 0
    assert harness.events.count('process') == 1
    assert harness.events.count('lock_closed') == 1


def test_cpu_fallback_latches_across_scheduler_restarts(harness, monkeypatch):
    def process(self, cancel):
        self.halted = 'gpu_residency_cpu_only'
        return ProcessResult('processed', 'observation', 'completed')
    monkeypatch.setattr(harness.Runner, 'process_one', process)
    assert harness.run() == 0
    assert json.loads((harness.state / 'halt.json').read_text())['reason'] == 'gpu_residency_cpu_only'
    first = list(harness.events)
    assert harness.run() == 0 and harness.events == first


def test_unexpected_failure_requests_scheduler_restart_and_redacts_exception(harness, monkeypatch):
    def crash(self, cancel): raise RuntimeError('sensitive-dsn')
    monkeypatch.setattr(harness.Runner, 'process_one', crash)
    assert harness.run() == 1
    receipt = (harness.state / 'service.json').read_text()
    assert 'sensitive-dsn' not in receipt and 'RuntimeError' in receipt


def test_stop_arriving_during_warmup_does_not_start_classification(harness, monkeypatch):
    def warm(runtime): (harness.state / 'stop.requested').touch()
    monkeypatch.setattr(service, 'ensure_runtime', warm)
    assert harness.run() == 0
    assert 'process' not in harness.events


def test_lock_lost_during_warmup_is_reacquired_before_classification(harness, monkeypatch):
    original = harness.Runner.__init__
    instances = []
    def init(self, *args):
        original(self, *args); instances.append(self)
        if len(instances) == 1:
            checks = []
            def check(query):
                checks.append(1)
                if len(checks) > 1: raise psycopg.OperationalError('lost during warm')
            self._lock_conn = SimpleNamespace(execute=check)
    monkeypatch.setattr(harness.Runner, '__init__', init)
    assert harness.run() == 0
    assert len(instances) == 2
    assert harness.events.count('process') == 1


def test_cpu_evidence_survives_failure_to_save_classification(harness, monkeypatch):
    client = SimpleNamespace(close=lambda: None,
                             residency=lambda: SimpleNamespace(state='cpu_only'))
    monkeypatch.setattr(service, 'make_client', lambda cfg: client)
    calls = []
    def process(self, cancel):
        calls.append(1)
        if len(calls) > 1: raise RuntimeError('unexpected retry after CPU fallback')
        self.classifier.residency()
        raise psycopg.OperationalError('failed classification write')
    monkeypatch.setattr(harness.Runner, 'process_one', process)
    assert harness.run() == 0
    assert json.loads((harness.state / 'halt.json').read_text())['reason'] == 'gpu_residency_cpu_only'
    first = list(harness.events)
    assert harness.run() == 0 and harness.events == first


@pytest.mark.parametrize('report', [
    {'overall': 'unowned_listener'},
    {'overall': 'problem', 'model': {'state': 'model_digest_mismatch'}},
    {'overall': 'up_gpu_not_demonstrated', 'gpu': {'residency': {'state': 'cpu_only'}}},
    {'overall': 'up_gpu_not_demonstrated', 'gpu': {'residency': {'state': 'partial_offload'}}},
])
def test_runtime_safety_failures_are_not_retried(report):
    runtime = SimpleNamespace(preflight=lambda: [], start=lambda **kwargs: {'exit': 0},
                              status=lambda **kwargs: report)
    with pytest.raises(service.SafetyHalt): service.ensure_runtime(runtime)


def test_remote_or_wrong_database_is_rejected():
    for dsn in ['host=example.com dbname=expected', 'host=127.0.0.1 dbname=other',
                'host=127.0.0.1 hostaddr=8.8.8.8 dbname=expected']:
        with pytest.raises(service.SafetyHalt): service.validate_dsn(dsn, 'expected')
    service.validate_dsn('host=127.0.0.1 port=45481 dbname=expected', 'expected')
