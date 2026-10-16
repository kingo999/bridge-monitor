"""Shared test harness: isolated temp world + fake Codex CLI."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE = ROOT / "tests" / "fake_codex.py"
FIXTURES = ROOT / "tests" / "fixtures"


class World:
    """A throw-away home, repo, state dir and fake-CLI scenario."""

    def __init__(self, scenario: dict | None = None) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge_v2_test_"))
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        self.state = self.tmp / "state"
        self.codex_home = self.tmp / "codex_home"
        for p in (self.home, self.codex_home):
            p.mkdir()
        self.repo.mkdir(parents=True)
        self.git("init", "-q")
        (self.repo / "README.md").write_text("# test repo\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "init")
        self.scenario_path = self.tmp / "scenario.json"
        self.log_path = self.tmp / "fake_codex.log"
        self.counter_path = self.tmp / "counter"
        self.scenario: dict = {"log": str(self.log_path), "counter": str(self.counter_path)}
        self.scenario.update(scenario or {})
        self.write_scenario()
        self.extra_env: dict[str, str] = {}

    def git(self, *args: str) -> str:
        import subprocess

        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def write_scenario(self) -> None:
        self.scenario_path.write_text(json.dumps(self.scenario), encoding="utf-8")

    def set(self, **kw) -> None:
        self.scenario.update(kw)
        self.write_scenario()

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": os.environ.get("TEMP", str(self.tmp)),
            "USERPROFILE": str(self.home),
            "HOME": str(self.home),
            "CODEX_HOME": str(self.codex_home),
            "CLAUDE_PROJECT_DIR": str(self.repo),
            "GPT_ARCHITECT_V2_STATE_DIR": str(self.state),
            "GPT_ARCHITECT_CODEX_PATH": str(FAKE),
            "FAKE_CODEX_SCENARIO": str(self.scenario_path),
        }
        env.update(self.extra_env)
        env.update(extra)
        return env

    # -- inspection ------------------------------------------------------
    def log_entries(self, kind: str | None = None) -> list[dict]:
        if not self.log_path.exists():
            return []
        rows = [json.loads(l) for l in self.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [r for r in rows if kind is None or r["kind"] == kind]

    def exec_entries(self) -> list[dict]:
        return [r for r in self.log_entries() if r["kind"].startswith("exec")]

    def telemetry(self) -> list[dict]:
        path = self.state / "telemetry" / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def telemetry_text(self) -> str:
        path = self.state / "telemetry" / "calls.jsonl"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def registry(self) -> dict:
        return json.loads((self.state / "registry.json").read_text(encoding="utf-8"))

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for(path: Path, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


class WorldCase(unittest.TestCase):
    scenario: dict | None = None

    def setUp(self) -> None:
        self.w = World(self.scenario)
        self.addCleanup(self.w.cleanup)

    def service(self, **env_extra: str):
        from bridge.service import ArchitectService

        return ArchitectService(self.w.env(**env_extra))
