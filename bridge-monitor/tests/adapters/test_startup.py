import sys
import os
import asyncio
from pathlib import Path

import pytest

from bridge.startup import build_coordinator
from tests.integration.test_ownership import _setup


def _runtime_file(project: Path) -> Path:
    config = project / "config" / "runtime.toml"
    config.parent.mkdir(parents=True)
    config.write_text("""[claude]
executable = "claude"
context_tokens = 200000
reported_models = ["claude-test"]
client_timeout_verified = false

[qwen]
context_tokens = 16384
byte_upper_bound_validated = false
client_timeout_verified = false
""", encoding="utf-8")
    return config


def test_parent_runtime_requires_recovery_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_DSN", "unused-test-dsn")
    monkeypatch.delenv("BRIDGE_MONITOR_SYSTEM_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SYSTEM_TOKEN"):
        build_coordinator(_runtime_file(tmp_path), audience="parent", project_root=tmp_path)


def test_parent_runtime_uses_active_policy_and_project_local_configuration(runtime_dsn,
                                                                             tmp_path,
                                                                             monkeypatch):
    _, version = _setup(runtime_dsn)
    monkeypatch.setenv("BRIDGE_MONITOR_DSN", runtime_dsn)
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "test-system-token")
    project = tmp_path / "bridge-monitor"
    project.mkdir()
    python = project / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake")
    coordinator = build_coordinator(_runtime_file(project), audience="parent",
        project_root=project, python_executable=python)
    assert coordinator.policy_version == version
    assert coordinator.claude_adapter is not None
    assert coordinator.qwen_adapter is None
    assert coordinator.emitter.spool.path.is_relative_to(project)


def test_runtime_refuses_configuration_outside_project(runtime_dsn, tmp_path,
                                                       monkeypatch):
    _setup(runtime_dsn)
    monkeypatch.setenv("BRIDGE_MONITOR_DSN", runtime_dsn)
    project = tmp_path / "bridge-monitor"
    project.mkdir()
    outside = _runtime_file(tmp_path / "other")
    with pytest.raises(ValueError, match="inside project"):
        build_coordinator(outside, audience="parent", project_root=project,
                          python_executable=Path(sys.executable))


def test_runtime_refuses_launch_without_reviewed_active_policy(runtime_dsn, tmp_path,
                                                                monkeypatch):
    monkeypatch.setenv("BRIDGE_MONITOR_DSN", runtime_dsn)
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "test-system-token")
    project = tmp_path / "bridge-monitor"
    project.mkdir()
    with pytest.raises(ValueError, match="reviewed policy"):
        build_coordinator(_runtime_file(project), audience="parent", project_root=project)


def test_mcp_entrypoint_reserves_stdout_for_protocol(monkeypatch, capsys):
    from bridge import mcp_server
    from bridge import startup
    events = []
    monkeypatch.setenv("BRIDGE_MONITOR_RUNTIME_CONFIG", "C:/AI/bridge-monitor/config/runtime.toml")
    monkeypatch.setattr(startup, "build_coordinator", lambda *args, **kwargs: "coordinator")
    class FakeServer:
        def run(self, *, transport):
            events.append(transport)
    monkeypatch.setattr(mcp_server, "create_mcp", lambda *args, **kwargs: FakeServer())
    assert mcp_server.main(["--audience", "parent"]) == 0
    assert events == ["stdio"]
    assert capsys.readouterr().out == ""


def test_real_stdio_handshake_exposes_only_audience_tools(runtime_dsn, tmp_path,
                                                          monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bridge.runtime.policy_startup import activate_policy
    from contracts.policy import validate_policy

    base, _ = _setup(runtime_dsn)
    policy = base.model_copy(update={"deployment": base.deployment.model_copy(update={
        "qwen_model_profile": "qwen-test", "qwen_model_digest": "a" * 64})})
    proposed = policy.model_dump()
    capabilities = {"claude-test": {"context_tokens": 10000}}
    digest = validate_policy(proposed, capabilities).snapshot.source_hash
    activate_policy(runtime_dsn, proposed, capabilities,
        approved_hash=digest, operator_id="operator-1")
    config = _runtime_file(tmp_path)
    monkeypatch.setenv("BRIDGE_MONITOR_DSN", runtime_dsn)
    monkeypatch.setenv("BRIDGE_MONITOR_RUNTIME_CONFIG", str(config))
    monkeypatch.setenv("BRIDGE_MONITOR_SYSTEM_TOKEN", "test-system-token")

    async def listed(audience):
        args = ["-m", "bridge.mcp_server", "--audience", audience]
        if audience == "claude":
            args += ["--parent-call-id", "parent-call", "--parent-lease-id", "parent-lease"]
        params = StdioServerParameters(command=sys.executable, args=args,
            env=os.environ.copy(), cwd=str(Path(__file__).parents[2]))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                return {item.name for item in (await session.list_tools()).tools}

    assert asyncio.run(listed("parent")) == {
        "delegate_claude", "complete_work", "get_execution_status", "cancel_work"}
    assert asyncio.run(listed("claude")) == {"delegate_local_analysis"}
