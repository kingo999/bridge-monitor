"""Run one ``codex exec`` turn: stream-parse JSONL, bound capture, enforce timeout.

The parser is deliberately tolerant (unknown event types and non-JSON lines are
counted, never fatal) but success is only ever declared by the caller after
*verified* completion; this module just reports what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

from . import codexcli, errors, telemetry
from .redact import redact  # noqa: F401  (re-exported for callers/tests)

KNOWN_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "item.started",
        "item.updated",
        "item.completed",
        "error",
    }
)
MAX_LINE_BYTES = 1_048_576
STDERR_TAIL_BYTES = 16_384
MAX_KEPT_MESSAGES = 5
MAX_MESSAGE_CHARS = 500

def _message_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("message", "error", "msg"):
            if key in value:
                return _message_of(value[key])
    return ""


@dataclass
class Events:
    total: int = 0
    non_json: int = 0
    oversize: int = 0
    unknown_types: int = 0
    thread_id: str | None = None
    thread_conflict: bool = False
    turn_completed: bool = False
    turn_failed: bool = False
    errors_before_completion: int = 0
    errors_after_completion: int = 0
    messages: list[str] = field(default_factory=list)
    usage: dict[str, int | None] = field(default_factory=telemetry.empty_usage)
    _usage_seen: bool = False

    def _keep(self, text: str) -> None:
        text = redact(text.strip())[:MAX_MESSAGE_CHARS]
        if text and len(self.messages) < MAX_KEPT_MESSAGES:
            self.messages.append(text)

    def feed(self, raw: bytes) -> None:
        try:
            event = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self.non_json += 1
            return
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self.non_json += 1
            return
        self.total += 1
        kind = event["type"]
        if kind not in KNOWN_EVENT_TYPES:
            self.unknown_types += 1
            return
        if kind == "thread.started":
            tid = event.get("thread_id")
            if isinstance(tid, str) and tid:
                if self.thread_id is None:
                    self.thread_id = tid
                elif self.thread_id != tid:
                    self.thread_conflict = True
        elif kind == "turn.completed":
            self.turn_completed = True
            usage = telemetry.normalise_usage(event.get("usage"))
            for name, value in usage.items():
                if value is not None:
                    self.usage[name] = (self.usage[name] or 0) + value
        elif kind == "turn.failed":
            self.turn_failed = True
            self._keep(_message_of(event.get("error", event)))
        elif kind == "error":
            if self.turn_completed:
                self.errors_after_completion += 1
            else:
                self.errors_before_completion += 1
            self._keep(_message_of(event))


@dataclass
class RunResult:
    returncode: int | None
    timed_out: bool
    cancelled: bool
    events: Events
    stderr_tail: str
    duration_s: float
    cleanup_ok: bool


def run_turn(
    argv: Sequence[str],
    prompt: str,
    env: Mapping[str, str],
    cwd: str,
    timeout_s: float,
    cancel: threading.Event,
) -> RunResult:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            **codexcli.popen_kwargs(),
        )
    except OSError as exc:
        raise errors.BridgeError(
            errors.SUBPROCESS_FAILED,
            f"Could not start the Codex CLI: {exc.__class__.__name__}.",
            code="spawn_failed",
            recovery="Check the Codex installation; no inference was attempted.",
        ) from exc

    events = Events()
    stderr_tail = bytearray()

    def write_stdin() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
        except Exception:  # noqa: BLE001 - child may have exited before reading
            pass

    def read_stdout() -> None:
        assert proc.stdout is not None
        try:
            skipping = False
            while True:
                line = proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    return
                complete = line.endswith(b"\n")
                if skipping:
                    skipping = not complete
                    continue
                if len(line) > MAX_LINE_BYTES and not complete:
                    events.oversize += 1
                    skipping = True
                    continue
                stripped = line.strip()
                if stripped:
                    events.feed(stripped)
        except Exception:  # noqa: BLE001
            return

    def read_stderr() -> None:
        assert proc.stderr is not None
        try:
            while True:
                chunk = proc.stderr.read(8192)
                if not chunk:
                    return
                stderr_tail.extend(chunk)
                if len(stderr_tail) > STDERR_TAIL_BYTES:
                    del stderr_tail[: len(stderr_tail) - STDERR_TAIL_BYTES]
        except Exception:  # noqa: BLE001
            return

    threads = [
        threading.Thread(target=fn, daemon=True, name=f"codex-{fn.__name__}")
        for fn in (write_stdin, read_stdout, read_stderr)
    ]
    for t in threads:
        t.start()

    deadline = started + timeout_s
    timed_out = cancelled = False
    cleanup_ok = True
    try:
        while proc.poll() is None:
            if cancel.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.05)
    finally:
        if proc.poll() is None or timed_out or cancelled:
            cleanup_ok = codexcli.kill_tree(proc)
    for t in threads[1:]:
        t.join(timeout=5)
    threads[0].join(timeout=1)
    for stream, t in zip((proc.stdin, proc.stdout, proc.stderr), threads):
        codexcli.close_quietly(stream, t)

    return RunResult(
        returncode=proc.returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        events=events,
        stderr_tail=redact(stderr_tail.decode("utf-8", errors="replace")),
        duration_s=time.monotonic() - started,
        cleanup_ok=cleanup_ok,
    )
