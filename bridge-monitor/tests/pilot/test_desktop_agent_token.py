"""Desktop MCP bootstrap forwards the configured agent credential to children."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/start-desktop-mcp.py"
SPEC = importlib.util.spec_from_file_location("desktop_mcp_launcher", SCRIPT)
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


def _policy(endpoint: str, token_ref: str = "env:BRIDGE_MONITOR_SKYNET_TOKEN"):
    return SimpleNamespace(deployment=SimpleNamespace(
        qwen_agent_endpoint=endpoint, qwen_agent_token_ref=token_ref))


def test_agent_route_loads_project_secret_into_named_environment(tmp_path, monkeypatch):
    secret = tmp_path / "var/secrets/agent-token"
    secret.parent.mkdir(parents=True)
    secret.write_text("test-token\n")
    monkeypatch.delenv("BRIDGE_MONITOR_SKYNET_TOKEN", raising=False)

    LAUNCHER.configure_agent_token(
        {"agent_token_file": "var/secrets/agent-token"},
        _policy("http://192.168.0.78:8874"), tmp_path,
    )

    assert os.environ["BRIDGE_MONITOR_SKYNET_TOKEN"] == "test-token"


def test_direct_route_does_not_require_agent_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("BRIDGE_MONITOR_SKYNET_TOKEN", raising=False)
    LAUNCHER.configure_agent_token({}, _policy(""), tmp_path)
    assert "BRIDGE_MONITOR_SKYNET_TOKEN" not in os.environ


def test_agent_secret_must_be_inside_project_secret_directory(tmp_path):
    (tmp_path / "var/secrets").mkdir(parents=True)
    outside = tmp_path / "other-token"
    outside.write_text("wrong-token")
    with pytest.raises(ValueError, match="project secret"):
        LAUNCHER.configure_agent_token(
            {"agent_token_file": str(outside)},
            _policy("http://192.168.0.78:8874"), tmp_path,
        )
