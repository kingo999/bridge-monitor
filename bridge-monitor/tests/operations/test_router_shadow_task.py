"""Project-local shadow task installation never exposes credentials in preview."""

import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def test_router_shadow_task_preview_is_nonsecret_and_read_only():
    task_name = "BridgeMonitor-RouterShadow"
    result = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
        str(PROJECT / "scripts" / "install-router-shadow-task.ps1"),
        "-ConfigPath", str(PROJECT / "config" / "router-shadow-task.example.json"),
    ], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert task_name in result.stdout
    assert "run-router-shadow-task.ps1" in result.stdout
    assert "BRIDGE_MONITOR_DSN" not in result.stdout
    assert "BRIDGE_MONITOR_SKYNET_TOKEN" not in result.stdout
