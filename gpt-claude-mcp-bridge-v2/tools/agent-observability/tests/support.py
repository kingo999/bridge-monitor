"""Shared fixtures for the OBS-01 tests. Everything is synthetic and isolated."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import obs_accounting  # noqa: E402
import obs_reader  # noqa: E402

FALLBACK_TMP = os.path.join(ROOT, ".tmp-obs-tests")
REPO = "0123456789abcdef01234567"
THREAD = "01a13958-8619-7bb1-8606-4d26caf17054"
MODEL = "gpt-6.1-sol"
BASE_TIME = datetime(2026, 10, 14, 7, 37, 40, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)

# The observed three-turn fixture (session cumulative counters).
THREE_TURNS = [
    {"input": 181960, "cached": 136448, "output": 3652, "reasoning": 1484, "run_ms": 141652, "resumed": False},
    {"input": 354513, "cached": 303872, "output": 4987, "reasoning": 1888, "run_ms": 53113, "resumed": True},
    {"input": 473409, "cached": 421376, "output": 5126, "reasoning": 1888, "run_ms": 14297, "resumed": True},
]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def call_id(n):
    return "%032x" % n


def success(n, input_tokens, cached, output, reasoning, run_ms=1000, resumed=True, thread=THREAD,
            name="default", repo_key=REPO, model=MODEL, minutes=None, cli="0.162.1", **over):
    start = BASE_TIME + timedelta(minutes=n * 10 if minutes is None else minutes)
    rec = {
        "schema_version": 1, "call_id": call_id(n), "tool": "consult_architect",
        "started_at": iso(start), "ended_at": iso(start + timedelta(seconds=(run_ms or 0) // 1000 + 4)),
        "route": {"model": model, "effort": "high", "model_source": "default", "effort_source": "default"},
        "session": {"name": name, "repo_key": repo_key, "thread_id": thread, "resumed": resumed},
        "queue_ms": 0, "run_ms": run_ms, "outcome": "success", "error_category": None, "error_code": None,
        "inference_may_have_started": True, "exit_code": 0,
        "usage": {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output,
                  "reasoning_output_tokens": reasoning, "total_tokens": None},
        "cli_version": cli, "warnings": [],
        "repo": {"before": "9586d5f2de90fb59", "after": "9586d5f2de90fb59", "changed": False,
                 "comparable": True, "head_before": "29183d87cf7f", "head_after": "29183d87cf7f",
                 "head_changed": False, "reason": None},
        "pointer_state": "bound", "history": "turn_may_exist", "session_state": "ready",
    }
    rec.update(over)
    return rec


def three_turn_records():
    out = []
    for i, t in enumerate(THREE_TURNS, start=1):
        out.append(success(i, t["input"], t["cached"], t["output"], t["reasoning"],
                           run_ms=t["run_ms"], resumed=t["resumed"]))
    return out


def failure(n, outcome="error", inference=True, thread=None, name="default", repo_key=REPO,
            model=MODEL, **over):
    start = BASE_TIME + timedelta(minutes=n * 10)
    rec = success(n, None, None, None, None, run_ms=None, resumed=thread is not None, thread=thread,
                  name=name, repo_key=repo_key, model=model)
    rec.update({
        "outcome": outcome, "error_category": "runtime", "error_code": "sandbox_setup_failed",
        "inference_may_have_started": inference, "exit_code": 1, "run_ms": None,
        "usage": {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
                  "reasoning_output_tokens": None, "total_tokens": None},
        "pointer_state": "not_reached" if not inference else "unchanged",
        "history": "turn_may_exist" if inference else "no_turn_started",
        "session_state": "ready", "ended_at": iso(start + timedelta(seconds=3)),
    })
    rec.update(over)
    return rec


class Workspace:
    """An isolated fake bridge state directory."""

    def __init__(self):
        self.root = self._make_root()
        self.state = os.path.join(self.root, "state")

    def _make_root(self):
        """Create the fixture root and its state/ child, or raise.

        tempfile.mkdtemp can succeed with an ACL that then denies creating children
        (Windows sandbox), so the whole initialisation is probed, not just mkdtemp.
        The fallback uses plain mkdir so the directories inherit their parent's ACLs.
        """
        candidate = None
        try:
            candidate = tempfile.mkdtemp(prefix="obs01-")
            os.mkdir(os.path.join(candidate, "state"))
            os.rmdir(os.path.join(candidate, "state"))
            os.mkdir(os.path.join(candidate, "state"))
            # realpath so ancestors are real directories (e.g. macOS /var -> /private/var)
            return os.path.realpath(candidate)
        except OSError:
            if candidate is not None:
                shutil.rmtree(candidate, ignore_errors=True)
        os.makedirs(FALLBACK_TMP, exist_ok=True)
        root = os.path.join(FALLBACK_TMP, "ws-%d-%d" % (os.getpid(), id(self)))
        os.mkdir(root)
        os.mkdir(os.path.join(root, "state"))
        return root

    def telemetry_dir(self):
        path = os.path.join(self.state, "telemetry")
        os.makedirs(path, exist_ok=True)
        return path

    def inflight_dir(self):
        path = os.path.join(self.state, "inflight")
        os.makedirs(path, exist_ok=True)
        return path

    def write_calls(self, records, name="calls.jsonl", raw_lines=None, trailing_newline=True):
        lines = [json.dumps(r) for r in records] + list(raw_lines or [])
        data = "\n".join(lines) + ("\n" if trailing_newline and lines else "")
        with open(os.path.join(self.telemetry_dir(), name), "w", encoding="utf-8", newline="") as fh:
            fh.write(data)

    def write_bytes(self, name, data):
        with open(os.path.join(self.telemetry_dir(), name), "wb") as fh:
            fh.write(data)

    def write_marker(self, name, obj):
        with open(os.path.join(self.inflight_dir(), name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        try:
            os.rmdir(FALLBACK_TMP)
        except OSError:
            pass


class _FakeLstat:
    """A stat_result look-alike that marks one path as a Windows reparse point."""

    def __init__(self, real, attributes=0x400):
        for name in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_nlink"):
            setattr(self, name, getattr(real, name))
        self.st_file_attributes = attributes


@contextlib.contextmanager
def simulated_reparse(*paths):
    """Make os.lstat report FILE_ATTRIBUTE_REPARSE_POINT for *paths* (no real links needed)."""
    wanted = {os.path.normcase(os.path.abspath(p)) for p in paths}
    real_lstat = os.lstat

    def fake(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if os.path.normcase(os.path.abspath(os.fspath(path))) in wanted:
            return _FakeLstat(st)
        return st

    with mock.patch.object(os, "lstat", fake):
        yield


def tree_digest(path):
    """Digest of every file name, size and content under *path* (to prove read-only behaviour)."""
    h = hashlib.sha256()
    for base, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            h.update(os.path.relpath(full, path).encode())
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def account(ws):
    state = obs_reader.read_state(ws.state)
    return obs_accounting.compute(state, NOW), state


def by_id(result, n):
    for call in result["calls"]:
        if call["call_id"] == call_id(n):
            return call
    raise AssertionError("call %d not found" % n)


class ObsTestCase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
