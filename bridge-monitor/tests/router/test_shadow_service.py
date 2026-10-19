"""The shadow service stays alive while its coordinator is unavailable."""

import importlib.util
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg


def test_coordinator_build_retries_after_database_failure(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "serve-router-shadow.py"
    spec = importlib.util.spec_from_file_location("shadow_service_test", path)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    calls = 0
    handlers = {}
    coordinator = SimpleNamespace(
        policy=SimpleNamespace(router=SimpleNamespace(mode="disabled", worker_poll_ms=1)),
        _refresh_policy=lambda: None)

    def build(_config):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise psycopg.OperationalError("database unavailable")
        return coordinator

    def sleep(_seconds):
        if calls == 2:
            handlers[signal.SIGTERM]()

    monkeypatch.setattr(service, "build_shadow_coordinator", build)
    monkeypatch.setattr(service.time, "sleep", sleep)
    monkeypatch.setattr(service.signal, "signal", lambda signum, handler:
                        handlers.__setitem__(signum, handler))
    monkeypatch.setattr(sys, "argv", [str(path), "--config", "dummy.toml"])
    assert service.main() == 0
    assert calls == 2


def test_uncertain_rebuild_retries_without_exiting(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "scripts" / "serve-router-shadow.py"
    spec = importlib.util.spec_from_file_location("shadow_service_rebuild_test", path)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    builds = 0
    handlers = {}
    coordinator = SimpleNamespace(
        policy=SimpleNamespace(router=SimpleNamespace(
            mode="shadow", worker_poll_ms=1, verifier=object())),
        project_root=tmp_path, _refresh_policy=lambda: None)

    def build(_config):
        nonlocal builds
        builds += 1
        if builds == 2:
            raise psycopg.OperationalError("rebuild unavailable")
        return coordinator

    def sleep(_seconds):
        if builds == 3:
            handlers[signal.SIGTERM]()

    monkeypatch.setattr(service, "build_shadow_coordinator", build)
    monkeypatch.setattr(service, "DockerVerifier", lambda *_: object())
    monkeypatch.setattr(service, "run_one_shadow", lambda *_:
                        {"state": "uncertain"} if builds == 1 else None)
    monkeypatch.setattr(service.time, "sleep", sleep)
    monkeypatch.setattr(service.signal, "signal", lambda signum, handler:
                        handlers.__setitem__(signum, handler))
    monkeypatch.setattr(sys, "argv", [str(path), "--config", "dummy.toml"])
    assert service.main() == 0
    assert builds == 3
