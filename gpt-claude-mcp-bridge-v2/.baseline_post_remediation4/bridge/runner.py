"""Run one ``codex exec`` turn: stream-parse JSONL, bound capture, own the process tree.

The parser is deliberately tolerant (unknown event types and non-JSON lines are counted,
never fatal) but success is only ever declared by the caller after *verified* completion;
this module just reports what happened.

Decided here, from the raw stream of the *actual* turn (not a pre-flight, not narration):

* **Infrastructure-failure detection (F1/N7).**  Sandbox-setup, process-creation and
  repository-access failures are recognised in (a) completed command/tool items that
  *report failure* (``status`` failed/error or non-zero ``exit_code``), (b) top-level
  ``error`` events, and (c) oversized lines (the WHOLE line is streamed through the scan, so
  a signature anywhere in it is found; status cannot be parsed, so an oversized record of a
  non-prose or unknown type that carries a signature is conservatively counted).  Agent
  narration and reasoning are never scanned - including an oversized record whose item type is
  recognised as such; an ordinary non-zero exit or a successful command whose output merely
  contains the words is not flagged.  The whole item is scanned (no prefix window) and every
  nested output container is traversed; only keys that carry what was *asked* of the tool
  (command, input, cwd, ...) are skipped.
* **Process-tree ownership (F3/N3/N4).**  The child is created *suspended*, placed in a Job
  Object, then resumed, so nothing runs un-owned.  If ownership cannot be established the
  child is killed unexecuted and the call is refused before any prompt byte is written.
  Everything after ``Popen`` is exception-safe: no path leaves a live child or job handle.
  Termination is ``not_needed`` / ``confirmed`` / ``unconfirmed`` from the job's own live
  count.  A timeout/cancel on Windows additionally sets ``unobservable_children_possible``:
  commands started by the sandbox *service* are outside the job and cannot be counted.
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
from .ids import is_thread_id
from .jobobject import CREATE_SUSPENDED, IS_WINDOWS, ProcessOwnership
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
OVERSIZE_HEAD_BYTES = 262_144      # retained only to classify the item type of an over-long line
STDERR_TAIL_BYTES = 16_384
MAX_KEPT_MESSAGES = 5
MAX_MESSAGE_CHARS = 500
MAX_ITEM_TEXT_CHARS = 2_000_000  # a line is at most MAX_LINE_BYTES; no prefix blind spot
#: how long job accounting may still show members after the parent exited before they are called survivors
EXIT_ACCOUNTING_GRACE_S = 0.5

# ---------------------------------------------------------------- signatures
#: item types that carry model-authored or model-visible *prose*: never scanned.
_PROSE_ITEM_TYPES = frozenset(
    {"agentmessage", "assistantmessage", "usermessage", "message", "reasoning", "todolist", "websearch"}
)
_FAILED_STATUSES = frozenset({"failed", "error", "errored", "declined", "rejected", "denied", "cancelled"})

INFRA_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sandbox_setup", re.compile(r"helper_unknown_error|setup refresh had errors|windows sandbox failed",
                                 re.IGNORECASE)),
    ("process_creation", re.compile(r"Failed to create unified exec process|CreateProcess\s*\{|"
                                    r"exec_command failed: CreateProcess", re.IGNORECASE)),
    ("repo_access", re.compile(r"fatal: not a git repository|fatal: cannot change to|"
                               r"Unable to read current working directory|fatal: unable to read tree|"
                               r"fatal: bad object|error: unable to read sha1 file|"
                               r"fatal: [^\r\n]{0,200}Permission denied", re.IGNORECASE)),
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


#: keys that carry what was *asked* of a tool (or identify it), not what it reported.  Everything
#: else in a failed command/tool item - including arbitrarily nested ``content`` / ``result``
#: containers - is output and is scanned (V7).  A signature inside the command itself (``git grep
#: helper_unknown_error``) must not count as an infrastructure failure.
_INPUT_KEYS = frozenset({
    "command", "cmd", "input", "arguments", "args", "argv", "patch", "prompt", "cwd",
    "type", "id", "call_id", "item_id",
})
_MAX_GATHER_DEPTH = 64
_MAX_GATHER_NODES = 200_000


def _gather_text_ex(value: Any) -> tuple[list[str], bool]:
    """(every output string in the item, truncated).  Iterative, so depth cannot overflow the stack;
    hitting a limit is reported (R4) instead of silently dropping what lies beyond it.  Only keys that
    carry what was *asked* of a tool are skipped - `files`, `path`, `status` etc. may hold output."""
    out: list[str] = []
    truncated = False
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_GATHER_NODES or depth > _MAX_GATHER_DEPTH:
            truncated = True
            continue
        if isinstance(current, str):
            out.append(current)
        elif isinstance(current, Mapping):
            for key, inner in current.items():
                if str(key).lower() not in _INPUT_KEYS:
                    stack.append((inner, depth + 1))
        elif isinstance(current, (list, tuple)):
            for inner in current:
                stack.append((inner, depth + 1))
    return out, truncated


def _gather_text(value: Any, depth: int = 0) -> list[str]:
    return _gather_text_ex(value)[0]


def signature_kinds(text: str, *, in_repo: bool = True) -> list[str]:
    return [name for name, pattern in INFRA_SIGNATURES
            if pattern.search(text) and (in_repo or name not in _REPO_BOUND_KINDS)]


def classify_item(item: Mapping[str, Any], repo_root: str | None) -> tuple[bool, list[str]]:
    """Return (is_command_like, infra_kinds) for one completed item."""
    command_like, kinds, _ = classify_item_ex(item, repo_root)
    return command_like, kinds


def classify_item_ex(item: Mapping[str, Any], repo_root: str | None) -> tuple[bool, list[str], bool]:
    """(is_command_like, infra_kinds, evidence_truncated) for one completed item."""
    kind = _norm_type(item.get("type"))
    if kind in _PROSE_ITEM_TYPES:
        return False, [], False
    status = str(item.get("status", "")).lower()
    exit_code = item.get("exit_code")
    failed = status in _FAILED_STATUSES or (isinstance(exit_code, int) and exit_code != 0)
    command_like = kind in {"commandexecution", "localshellcall", "exec", "execcommand", "mcptoolcall",
                            "toolcall", "functioncall", "customtoolcall", "filechange", "patchapply", "error"}
    if not failed and kind != "error":
        return command_like, [], False
    pieces, truncated = _gather_text_ex(item)
    text = "\n".join(pieces)[:MAX_ITEM_TEXT_CHARS]
    if not text:
        return command_like, [], truncated
    cwd = item.get("cwd")
    in_repo = True
    if isinstance(cwd, str) and repo_root:
        here, root = _norm_path(cwd), _norm_path(repo_root)
        in_repo = here == root or here.startswith(root + "\\")
    return command_like, signature_kinds(text, in_repo=in_repo), truncated


def _message_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("message", "error", "msg"):
            if key in value:
                return _message_of(value[key])
    return ""


#: The item type is read ONLY from the canonical event prefix `{"type":"item.<phase>","item":{["id":"..",]
#: "type":"<T>"`: a structurally anchored match.  An earlier nested `item` object elsewhere in the line
#: cannot be mistaken for the event's own item (R4); anything else is "unknown" and treated conservatively.
_ITEM_TYPE_RE = re.compile(
    rb'\A\s*\{\s*"type"\s*:\s*"item\.[A-Za-z_]+"\s*,\s*"item"\s*:\s*\{\s*'
    rb'(?:"id"\s*:\s*"[^"\\]{0,128}"\s*,\s*)?"type"\s*:\s*"([A-Za-z0-9_.\-]{1,64})"')
_UNICODE_ESCAPE = re.compile(rb"\\u00([0-7][0-9a-fA-F])")    # JSON \u00XX for ASCII
#: >= the longest signature (repo_access allows ~230 characters) times the 6-byte escape expansion
_OVERSIZE_WINDOW = 2048


class OversizeScan:
    """Streams an over-long JSONL line through the infrastructure-signature scan.

    Only a bounded head is retained (to classify the item type); every byte is scanned through a
    sliding window, so a signature in the *middle* of the line is found as reliably as one at the
    ends (V7: the previous head/tail scan missed it)."""

    def __init__(self, first: bytes) -> None:
        self.head = first[:OVERSIZE_HEAD_BYTES]
        self.kinds: set[str] = set()
        self.total = 0
        self._carry = b""
        self._scan(first)
        m = _ITEM_TYPE_RE.search(self.head[:OVERSIZE_HEAD_BYTES])
        self.item_type: str | None = _norm_type(m.group(1).decode("ascii", "replace")) if m else None

    def _scan(self, chunk: bytes) -> None:
        data = self._carry + chunk
        self.total += len(chunk)
        # scan the text as written AND with JSON ASCII escapes (\u005f -> _) resolved: a signature
        # encoded with escapes must not evade the scan (R4)
        self.kinds.update(signature_kinds(data.decode("utf-8", errors="replace")))
        decoded = _UNICODE_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), data)
        if decoded != data:
            self.kinds.update(signature_kinds(decoded.decode("utf-8", errors="replace")))
        self._carry = data[-_OVERSIZE_WINDOW:]

    def feed(self, chunk: bytes) -> None:
        self._scan(chunk)


@dataclass
class Events:
    repo_root: str | None = None
    on_thread: Callable[[str], None] | None = None
    total: int = 0
    non_json: int = 0
    oversize: int = 0
    oversize_infra: int = 0
    oversize_prose_skipped: int = 0
    #: R4: the stdout reader died mid-stream (the tail of the turn was never seen)
    reader_failed: bool = False
    #: failed items whose output traversal hit a limit (evidence beyond it was not read)
    evidence_truncated_items: int = 0
    unknown_types: int = 0
    thread_id: str | None = None
    thread_invalid: bool = False
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

    def _count_infra(self, kinds: list[str]) -> None:
        if kinds:
            self.infra_failed_items += 1
            self.infra_kinds.update(kinds)

    def feed_oversize(self, scan: "OversizeScan") -> None:
        """An over-long line could not be parsed as JSON.

        The WHOLE line was streamed through a signature scan (nothing is skipped), so a signature
        anywhere in it is found.  Status/exit code cannot be read, so the policy is conservative:
        an oversized record of an unknown or non-prose type that carries a signature is counted
        (a >1 MiB line that merely *contains* the words is a false positive we accept).  An
        oversized record whose item type is recognised as agent narration/reasoning is excluded,
        exactly like a parsed prose item."""
        if scan.item_type in _PROSE_ITEM_TYPES:
            self.oversize_prose_skipped += 1
            return
        if scan.kinds:
            self.oversize_infra += 1
            self._count_infra(sorted(scan.kinds))

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
            if not is_thread_id(tid):
                self.thread_invalid = True      # never stored, echoed, journalled or logged
            elif self.thread_id is None:
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
                command_like, kinds, truncated = classify_item_ex(item, self.repo_root)
                if command_like:
                    self.command_items += 1
                if truncated:
                    self.evidence_truncated_items += 1
                self._count_infra(kinds)
        elif kind == "turn.completed":
            self.turn_completed = True
            usage = telemetry.normalise_usage(event.get("usage"))
            for name, value in usage.items():
                if value is not None:
                    self.usage[name] = (self.usage[name] or 0) + value
        elif kind == "turn.failed":
            self.turn_failed = True
            text = _message_of(event.get("error", event))
            self._keep(text)
            self.infra_kinds.update(signature_kinds(text))   # context for the failure message
        elif kind == "error":
            text = _message_of(event)
            if self.turn_completed:
                self.errors_after_completion += 1
            else:
                self.errors_before_completion += 1
            self._keep(text)
            self._count_infra(signature_kinds(text))         # N7: infra-bearing error events


@dataclass
class RunResult:
    returncode: int | None
    timed_out: bool
    cancelled: bool
    events: Events
    stderr_tail: str
    duration_s: float
    cleanup_ok: bool
    #: "job_object" | "process_group"  (how the process tree was owned; never "none" here)
    ownership: str = "none"
    ownership_error: str | None = None
    #: "not_needed" | "confirmed" | "unconfirmed"
    termination: str = "not_needed"
    #: live owned processes still present after the parent CLI exited (terminated by us)
    reaped_descendants: int = 0
    #: live owned processes after cleanup (None = unknown)
    active_after: int | None = None
    child_pid: int | None = None
    #: True when the turn was interrupted on Windows: commands launched by the sandbox
    #: *service* are outside the job and can neither be counted nor confirmed gone
    unobservable_children_possible: bool = False


def _refuse(proc: subprocess.Popen[bytes], owner: ProcessOwnership, why: str) -> errors.BridgeError:
    """Kill an un-owned, never-resumed child and build the refusal (no prompt byte was written)."""
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    codexcli.kill_tree(proc)
    return errors.BridgeError(
        errors.SUBPROCESS_FAILED,
        f"Process ownership could not be established ({why}); the Codex child was killed before it "
        "executed and before any prompt was written.",
        code="ownership_unavailable",
        inference_may_have_started=False,
        recovery="Refusing to run a turn whose process tree cannot be owned or confirmed terminated. "
                 "Check that the bridge itself is not inside a restrictive job object. No model call was made.",
    )


def _terminate_owned(owner: ProcessOwnership | None, proc: subprocess.Popen[bytes]) -> None:
    """Best effort, genuinely never raises (R10: the poll is inside the guard too): kill every owned
    process (even when the root already exited, which is exactly when a surviving descendant would
    otherwise be missed) and then the root."""
    if owner is not None:
        try:
            owner.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        running = proc.poll() is None
    except Exception:  # noqa: BLE001
        running = True        # cannot tell: assume it is, and try to kill it
    if running:
        try:
            codexcli.kill_tree(proc)
        except Exception:  # noqa: BLE001
            pass


def _still_running(proc: subprocess.Popen[bytes]) -> bool:
    try:
        return proc.poll() is None
    except Exception:  # noqa: BLE001
        return True


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
    kwargs = codexcli.popen_kwargs()
    if IS_WINDOWS:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_SUSPENDED
    # R10: ALL bookkeeping is created BEFORE the child exists, so nothing can fail (allocation,
    # name binding) between Popen returning and the protected scope beginning.
    owner: ProcessOwnership | None = None
    events: Events | None = None
    stderr_tail = bytearray()
    started_threads: list[threading.Thread] = []   # only threads whose start() succeeded
    writer: threading.Thread | None = None
    reader_out: threading.Thread | None = None
    reader_err: threading.Thread | None = None
    prompt_started = False
    timed_out = cancelled = killed = False
    cleanup_ok = True
    reaped = 0
    active_after: int | None = None
    termination = "not_needed"
    clean = False
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            **kwargs,
        )
    except OSError as exc:
        raise errors.BridgeError(
            errors.SUBPROCESS_FAILED,
            f"Could not start the Codex CLI: {exc.__class__.__name__}.",
            code="spawn_failed",
            recovery="Check the Codex installation; no inference was attempted.",
        ) from exc

    # From here a child exists (suspended on Windows).  EVERYTHING below - including building the
    # helper objects and starting the reader/writer threads - is inside the cleanup scope, and
    # the job handle is released by an outermost ``finally`` (V1).
    try:
        owner = ProcessOwnership()
        events = Events(repo_root=cwd, on_thread=on_thread)

        # ---- ownership first: nothing executes, and no prompt byte is written, un-owned ----
        if not owner.attach(proc):
            raise _refuse(proc, owner, owner.error or "attach failed")
        if IS_WINDOWS and not owner.resume(proc):
            raise _refuse(proc, owner, owner.error or "resume failed")
        if on_started:
            try:
                on_started({"child_pid": proc.pid, "ownership": owner.mechanism, "ownership_error": owner.error})
            except Exception:  # noqa: BLE001
                pass

        def write_stdin() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
            except Exception:  # noqa: BLE001 - child may have exited before reading
                pass

        def read_stdout() -> None:
            assert proc.stdout is not None and events is not None
            try:
                scan: OversizeScan | None = None
                while True:
                    line = proc.stdout.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        if scan is not None:
                            events.feed_oversize(scan)
                        return
                    complete = line.endswith(b"\n")
                    if scan is not None:
                        scan.feed(line)
                        if complete:
                            events.feed_oversize(scan)
                            scan = None
                        continue
                    if len(line) > MAX_LINE_BYTES and not complete:
                        events.oversize += 1
                        scan = OversizeScan(line)
                        continue
                    stripped = line.strip()
                    if stripped:
                        events.feed(stripped)
            except Exception:  # noqa: BLE001
                events.reader_failed = True      # never silent: the turn's tail was not read (R4)
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

        # readers first, the prompt writer LAST: a failed start can never have written a prompt byte
        reader_out = threading.Thread(target=read_stdout, daemon=True, name="codex-read_stdout")
        reader_out.start()
        started_threads.append(reader_out)
        reader_err = threading.Thread(target=read_stderr, daemon=True, name="codex-read_stderr")
        reader_err.start()
        started_threads.append(reader_err)
        writer = threading.Thread(target=write_stdin, daemon=True, name="codex-write_stdin")
        writer.start()
        started_threads.append(writer)
        prompt_started = True

        deadline = started + timeout_s
        while proc.poll() is None:
            if cancel.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.05)

        # ---- termination: only things we own (never unrelated processes) ----------------
        if proc.poll() is None:
            killed = True
            owner.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cleanup_ok = False

        # ---- after the parent exited: do NOT assume the tree is gone ----------------------
        active = owner.active_processes()
        # Job accounting lags process teardown (measured: the count is still 1 for ~4 ms after a
        # normal exit of a launcher + interpreter pair).  A count seen at this instant is therefore
        # not evidence of a survivor; only a count that persists past a short grace window is.
        grace_until = time.monotonic() + EXIT_ACCOUNTING_GRACE_S
        while active and time.monotonic() < grace_until:
            time.sleep(0.01)
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
        clean = True
    except errors.BridgeError:
        raise
    except Exception as exc:  # noqa: BLE001 - setup/IO failure after the child exists
        termination = "unconfirmed"
        err = errors.BridgeError(
            errors.SUBPROCESS_FAILED,
            f"The bridge failed while supervising the Codex process ({exc.__class__.__name__}); "
            "the child and its job were terminated.",
            code="runner_failed",
            inference_may_have_started=prompt_started,
            recovery="No retry was made.",
        )
        raise err from exc
    finally:
        # exception-safe on EVERY path: no live child, no leaked job handle, no join on a thread
        # that never started, and the original (categorised) failure is never masked.
        try:
            if not clean or _still_running(proc):
                _terminate_owned(owner, proc)
                if clean:
                    termination = "unconfirmed"
            for t in started_threads:
                try:
                    t.join(timeout=1 if t is writer else 5)
                except Exception:  # noqa: BLE001
                    pass
            codexcli.close_quietly(proc.stdin, writer)
            codexcli.close_quietly(proc.stdout, reader_out)
            codexcli.close_quietly(proc.stderr, reader_err)
        finally:
            if owner is not None:
                try:
                    owner.close()
                except Exception:  # noqa: BLE001
                    pass

    assert events is not None and owner is not None
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
        unobservable_children_possible=bool(IS_WINDOWS and (timed_out or cancelled)),
    )
