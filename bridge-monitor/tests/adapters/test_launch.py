import json
from pathlib import Path

import pytest

from bridge.launch import create_child_mcp_config, create_parent_mcp_config
from contracts.work import DispatchLease


def _lease():
    return DispatchLease(lease_id="lease-1", call_id="call-1",
                         owner_incarnation="boot-1", fencing_token=1,
                         policy_version="policy-v1")


def test_child_config_is_project_local_and_scoped_to_parent_lease(tmp_path):
    project = tmp_path / "bridge-monitor"
    python = project / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake executable")
    target = create_child_mcp_config(project, _lease(), python_executable=python)
    assert target.is_relative_to(project)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert list(data["mcpServers"]) == ["bridge_monitor"]
    server = data["mcpServers"]["bridge_monitor"]
    assert server["command"] == str(python)
    assert server["args"] == ["-m", "bridge.mcp_server", "--audience", "claude",
                              "--parent-call-id", "call-1", "--parent-lease-id", "lease-1"]
    assert "env" not in server


def test_child_config_rejects_executable_outside_project(tmp_path):
    project = tmp_path / "bridge-monitor"
    project.mkdir()
    outside = tmp_path / "other" / "python.exe"
    outside.parent.mkdir()
    outside.write_bytes(b"fake")
    with pytest.raises(ValueError, match="inside project"):
        create_child_mcp_config(project, _lease(), python_executable=outside)


def test_parent_config_is_project_local_and_exposes_only_parent_server(tmp_path):
    project = tmp_path / "bridge-monitor"
    python = project / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake executable")
    target = create_parent_mcp_config(project, python_executable=python)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert target == project / "var" / "mcp" / "parent.json"
    assert data == {"mcpServers": {"bridge_monitor": {
        "command": str(python),
        "args": ["-m", "bridge.mcp_server", "--audience", "parent"]}}}
    assert create_parent_mcp_config(project, python_executable=python) == target
