"""Project-local MCP configuration for a single Claude execution lease."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def create_parent_mcp_config(project_root: str | Path, *,
                             python_executable: str | Path) -> Path:
    project = Path(project_root).resolve(strict=True)
    executable = Path(python_executable).resolve(strict=True)
    if not executable.is_relative_to(project):
        raise ValueError("parent executable must be inside project")
    target = project / "var" / "mcp" / "parent.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"mcpServers": {"bridge_monitor": {"command": str(executable),
        "args": ["-m", "bridge.mcp_server", "--audience", "parent"]}}}
    rendered = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != rendered:
            raise ValueError("parent configuration identity conflict")
        return target
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                     dir=target.parent, delete=False) as pending:
        pending.write(rendered)
        temporary = Path(pending.name)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def create_child_mcp_config(project_root: str | Path, lease, *,
                            python_executable: str | Path) -> Path:
    project = Path(project_root).resolve(strict=True)
    executable = Path(python_executable).resolve(strict=True)
    if not executable.is_relative_to(project):
        raise ValueError("child executable must be inside project")
    if not lease.lease_id or not lease.call_id:
        raise ValueError("parent lease binding is required")
    folder = project / "var" / "mcp" / hashlib.sha256(
        lease.lease_id.encode("utf-8")).hexdigest()[:32]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "child.json"
    data = {"mcpServers": {"bridge_monitor": {
        "command": str(executable),
        "args": ["-m", "bridge.mcp_server", "--audience", "claude",
                 "--parent-call-id", lease.call_id,
                 "--parent-lease-id", lease.lease_id],
    }}}
    rendered = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != rendered:
            raise ValueError("lease configuration identity conflict")
        return target
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                     dir=folder, delete=False) as pending:
        pending.write(rendered)
        temporary = Path(pending.name)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
