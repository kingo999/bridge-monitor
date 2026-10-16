"""Run one ``codex exec`` turn: stream-parse JSONL, bound capture, own the process tree.

The parser is deliberately tolerant (unknown event types and non-JSON lines are
counted, never fatal) but success is only ever declared by the caller after
*verified* completion; this module just reports what happened.

Two things beyond plain parsing live here because they must be decided from the raw
stream of the *actual* turn, not from a pre-flight and not from the model's narration:

* **Infrastructure-failure detection (F1).**  Command/tool items whose own output shows
  sandbox setup, process-creation or repository-access failures are counted by kind.  Only
  items that report failure (``status`` failed/error or a non-zero ``exit_code``) are
  considered, and agent narration / reasoning is never scanned, so an ordinary non-zero
  exit (``git diff`` exit 1) or a file that merely *contains* these words is not flagged.
* **Process-tree ownership (F3).**  The child is placed in a Job Object (see jobobject.py).
  Termination is reported as ``not_needed`` / ``confirmed`` / ``unconfirmed`` and is
  confirmed only from the job's own live-process count.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import re
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote

from . import codexcli, errors, telemetry
from .jobobject import ProcessOwnership
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
MAX_ITEM_TEXT_CHARS = 8_192

# ---------------------------------------------------------------- F1 signatures
#: item types that carry model-authored or model-visible *prose*: never scanned.
_PROSE_ITEM_TYPES = frozenset(
    {"agentmessage", "assistantmessage", "usermessage", "message", "reasoning", "todolist", "websearch"}
)
_FAILED_STATUSES = frozenset({"failed", "error", "errored", "declined", "rejected", "denied", "cancelled"})
_TEXT_FIELDS = ("aggregated_output", "output", "stderr", "stdout", "error", "message", "result", "detail")

INFRA_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sandbox_setup", re.compile(r"helper_unknown_error|setup refresh had errors|windows sandbox failed",
                                 re.IGNORECASE)),
    ("process_creation", re.compile(r"Failed to create unified exec process|CreateProcess\s*\{|"
                                    r"exec_command failed: CreateProcess", re.IGNORECASE)),
    ("repo_access", re.compile(r"fatal: not a git repository|fatal: cannot change to|"
                               r"Unable to read current working directory", re.IGNORECASE)),
)
_REPO_BOUND_KINDS = frozenset({"repo_access"})  # only meaningful when cwd is the repository


def _norm_type(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def _norm_path(value: str) -> str:
    text = unquote(value)
    for prefix in ("file:///", "file://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    return text.replace("/", "\\").rstrip("\\").lower().lstrip("\\")


def _gather_text(value: Any, depth: int = 0) -> list[str]:
    out: list[str] = []
    if depth > 3:
        return out
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, Mapping):
        for key in _TEXT_FIELDS:
            if key in value:
                out.extend(_gather_text(value[key], depth + 1))
        if "text" in value and isinstance(value["text"], str):
            out.append(value["text"])
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_gather_text(item, depth + 1))
    return out


def classify_item(item: Mapping[str, Any], repo_root: str | None) -> tuple[bool, list[str]]:
    """Return (is_command_like, infra_kinds) for one completed item."""
    kind = _norm_type(item.get("type"))
    if kind in _PROSE_ITEM_TYPES:
        return False, []
    status = str(item.get("status", "")).lower()
    exit_code = item.get("exit_code")
    failed = status in _FAILED_STATUSES or (isinstance(exit_code, int) and exit_code != 0)
    command_like = kind in {"commandexecution", "localshellcall", "exec", "execcommand", "mcptoolcall",
                            "toolcall", "functioncall", "customtoolcall", "filechange", "patchapply", "error"}
    if not failed and kind != "error":
        return command_like, []
    text = "\n".join(_gather_text(item))[:MAX_ITEM_TEXT_CHARS]
    if not text:
        return command_like, []
    cwd = item.get("cwd")
    in_repo = True
    if isinstance(cwd, str) and repo_root:
        in_repo = _norm_path(cwd).startswith(_norm_path(repo_root))
    kinds = []
    for name, pattern in INFRA_SIGNATURES:
        if pattern.search(text) and (in_repo or name not in _REPO_BOUND_KINDS):
            kinds.append(name)
    return command_like, kinds


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
    repo_root: str | None = None
    on_thread: Callable[[str], None] | None = None
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
    command_items: int = 0
    infra_failed_items: int = 0
    infra_kinds: Counter = field(default_factory=Counter)
    messages: list[str] = field(default_factory=list)
    usage: dict[str, int | None] = field(default_factory=telemetry.empty_usage)

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
                    if self.on_thread:
                        try:
                            self.on_thread(tid)
                        except Exception:  # noqa: BLE001 - observers must not break parsing
                            pass
                elif self.thread_id != tid:
                    self.thread_conflict = True
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, Mapping):
                command_like, kinds = classify_item(item, self.repo_root)
                if command_like:
                    self.command_items += 1
                if kinds:
                    self.infra_failed_items += 1
                    self.infra_kinds.update(kinds)
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
    #: "job_object" | "process_group" | "none"  (how the process tree was owned)
    ownership: str = "none"
    ownership_error: str | None = None
    #: "not_needed" | "confirmed" | "unconfirmed"
    termination: str = "not_needed"
    #: owned processes still alive after the parent CLI exited (terminated by us)
    reaped_descendants: int = 0
    #: live owned processes after cleanup (None = unknown)
    active_after: int | None = None
    child_pid: int | None = None


def run_turn(
    argv: Sequence[str],
    prompt: str,
    env: Mapping[str, str],
    cwd: str,
    timeout_s: float,
    cancel: threading.Event,
    *,
    on_started: Callable[[dict[str, Any]], None] | None = None,
    on_thread: Callable[[str], None] | None = None,
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

    owner = ProcessOwnership()
    owner.attach(proc)
    if on_started:
        try:
            on_started({"child_pid": proc.pid, "ownership": owner.mechanism, "ownership_error": owner.error})
        except Exception:  # noqa: BLE001
            pass

    events = Events(repo_root=cwd, on_thread=on_thread)
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
    timed_out = cancelled = killed = False
    cleanup_ok = True
    reaped = 0
    active_after: int | None = None
    termination = "not_needed"
    try:
        while proc.poll() is None:
            if cancel.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.05)

        # --- termination: only things we own (never unrelated processes) -------------
        if proc.poll() is None:
            killed = True
            if owner.owned:
                owner.terminate()
            else:
                cleanup_ok = codexcli.kill_tree(proc)  # taskkill /T fallback: attempted only
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cleanup_ok = False

        # --- after the parent exited: do NOT assume the tree is gone ------------------
        if owner.owned:
            active = owner.active_processes()
            if active:
                reaped = active
                owner.terminate()
                for _ in range(100):  # <= 5 s
                    active = owner.active_processes()
                    if active == 0:
                        break
                    time.sleep(0.05)
            active_after = owner.active_processes()
            if active_after == 0:
                termination = "confirmed" if (killed or reaped) else "not_needed"
            else:
                termination = "unconfirmed"
        elif killed:
            termination = "unconfirmed"  # taskkill attempted; descendants cannot be counted
    finally:
        if proc.poll() is None:
            owner.terminate()
            codexcli.kill_tree(proc)
            termination = "unconfirmed"
        for t in threads[1:]:
            t.join(timeout=5)
        threads[0].join(timeout=1)
        for stream, t in zip((proc.stdin, proc.stdout, proc.stderr), threads):
            codexcli.close_quietly(stream, t)
        owner.close()

    return RunResult(
        returncode=proc.returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        events=events,
        stderr_tail=redact(stderr_tail.decode("utf-8", errors="replace")),
        duration_s=time.monotonic() - started,
        cleanup_ok=cleanup_ok and termination != "unconfirmed",
        ownership=owner.mechanism,
        ownership_error=owner.error,
        termination=termination,
        reaped_descendants=reaped,
        active_after=active_after,
        child_pid=proc.pid,
    )
