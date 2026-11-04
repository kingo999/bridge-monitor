"""Behaviour of scripts/serve-dashboard.py main() for the Design Mode owner binding.

Runs the real launcher against a temporary TOML with fake, test-only environment values. The real
create_app (and its shared validate_design_mode_owner_id) runs; uvicorn is replaced, so no server
starts and no host credentials or database are touched.
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "scripts" / "serve-dashboard.py"
ENV = {"dsn": "BM_LAUNCHER_TEST_DSN", "operator": "BM_LAUNCHER_TEST_OPERATOR",
       "producers": "BM_LAUNCHER_TEST_PRODUCERS"}


def _config(tmp_path, owner_line=""):
    text = f"""
[listen]
host = "127.0.0.1"
port = 8873
tls_cert_path = ""
tls_key_path = ""

[operator]
credential_env = "{ENV['operator']}"
session_ttl_seconds = 3600

[capture]
dsn_env = "{ENV['dsn']}"
producer_tokens_env = "{ENV['producers']}"
payload_limit_bytes = 1048576
subscription_producer_ids = ["fake-producer"]

[dashboard]
export_dir = "dashboard/out"
work_item_max_limit = 1000
{owner_line}
"""
    path = tmp_path / "dashboard-service.test.local.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("serve_dashboard_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_create_app = module.create_app
    seen = {"create_app": [], "run": []}

    def create_app_spy(dsn, **kwargs):
        seen["create_app"].append(dict(kwargs, dsn=dsn))
        # The built export is a local artifact; keep the test hermetic and let the real
        # create_app validate everything else, including design_mode_owner_id.
        forwarded = dict(kwargs, dashboard_dir=None)
        app = real_create_app(dsn, **forwarded)
        seen["app"] = app
        return app

    def fake_run(app, **kwargs):
        seen["run"].append((app, kwargs))

    monkeypatch.setattr(module, "create_app", create_app_spy)
    monkeypatch.setattr(module, "uvicorn", types.SimpleNamespace(run=fake_run))
    monkeypatch.delenv("BRIDGE_MONITOR_SERVER_PID_FILE", raising=False)
    monkeypatch.setenv(ENV["dsn"], "postgresql://fake@127.0.0.1/bridge_monitor_test_fake")
    monkeypatch.setenv(ENV["operator"], "operator-test-fake")
    monkeypatch.setenv(ENV["producers"], json.dumps({"fake-producer": "producer-test-fake"}))

    def run(config):
        monkeypatch.setattr(sys, "argv", ["serve-dashboard.py", "--config", str(config)])
        module.main()
        return seen

    run.seen = seen
    return run


def test_missing_owner_passes_none_and_starts(launcher, tmp_path):
    seen = launcher(_config(tmp_path))
    [call] = seen["create_app"]
    assert "design_mode_owner_id" in call and call["design_mode_owner_id"] is None
    assert call["dashboard_dir"] == (PROJECT / "dashboard" / "out").resolve()
    [(app, kwargs)] = seen["run"]
    assert app is seen["app"] and kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8873


@pytest.mark.parametrize("owner", ["bridge-monitor-parent@dave", "Harness-Producer@Test.Account"])
def test_valid_owner_is_passed_exactly(launcher, tmp_path, owner):
    seen = launcher(_config(tmp_path, f"design_mode_owner_id = {json.dumps(owner)}"))
    [call] = seen["create_app"]
    assert call["design_mode_owner_id"] == owner
    assert len(seen["run"]) == 1 and seen["run"][0][0] is seen["app"]


@pytest.mark.parametrize("raw", ['""', '"   "', '" owner@host"', '"owner@host\\n"', "7", "true",
                                 '["owner@host"]'])
def test_invalid_owner_fails_shared_validation_before_uvicorn(launcher, tmp_path, raw):
    with pytest.raises(ValueError, match="design_mode_owner_id"):
        launcher(_config(tmp_path, f"design_mode_owner_id = {raw}"))
    assert len(launcher.seen["create_app"]) == 1
    assert launcher.seen["run"] == []
