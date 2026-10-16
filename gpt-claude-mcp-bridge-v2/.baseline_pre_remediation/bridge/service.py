"""One architect call, end to end.

Flow (every step before ``run_turn`` is non-inference and can fail cleanly):

    validate input -> resolve route -> canonical repo + state dir -> session lock
    -> registry entry -> CLI version/compat -> auth -> catalog validation
    -> capability audit -> argv -> run -> verify completion -> bind thread
    -> telemetry (exactly once, in ``finally``)

There is no retry anywhere: once ``run_turn`` has been entered the call is
never replayed, whatever happens to telemetry or state afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Mapping
import uuid

from . import codexcli, config, errors, runner, state, telemetry

log = logging.getLogger("architect_bridge")  # stderr only; stdout is MCP

# Byte-for-byte the v1 instructions, so the model-side behaviour is unchanged.
BASE_INSTRUCTIONS = """You are the GPT ARCHITECT reviewing a local software project.
Your responsibility is architecture, contracts, consistency and critical review.
Claude Code is the implementer. You are read-only: NEVER write, delete or edit
repository files, and do not execute commands that change the repo or system.
Read source and architecture documents only when relevant; prioritize AGENTS.md,
architecture/*.md and known implementation contracts when they exist.
Be precise: cite relevant repository paths/symbols; distinguish verified facts
from design recommendations and unknowns. Do not invent code or test results.
For any change to an agreed design, present the proposal and ask for the human
architect's approval; do not treat Claude's request as that approval.
For deterministic systems: preserve deterministic data interpretation and rules.
Keep answers focused on the actual implementation question.
"""

_REVIEW_PREFIX = (
    "\nReview the currently staged and unstaged Git diffs, and relevant source. "
    "If no changes exist, say so. Prioritize correctness, regression risk, "
    "architecture contract violations, security and missing negative tests. "
    "Do not apply fixes.\nSpecific focus:\n"
)

_RATE_LIMIT = re.compile(
    r"rate[ _-]?limit|usage limit|too many requests|\b429\b|quota (exceeded|exhausted)|"
    r"(exceeded|hit|reached) (your|the) .{0,40}limit",
    re.IGNORECASE,
)
_AUTH_RUNTIME = re.compile(
    r"not logged in|unauthori[sz]ed|\b401\b|token (has )?expired|refresh token|"
    r"please (log|sign) ?in|invalid .{0,20}credentials",
    re.IGNORECASE,
)
_SESSION_MISSING = re.compile(
    r"(session|thread|conversation|rollout).{0,40}(not found|no such|does not exist|could not be found|missing)|"
    r"no (saved )?(session|rollout)",
    re.IGNORECASE,
)


def compose_prompt(tool: str, text: str) -> str:
    if tool == config.CONSULT:
        return BASE_INSTRUCTIONS + "\nArchitecture consultation:\n" + text
    if tool == config.REVIEW:
        return BASE_INSTRUCTIONS + _REVIEW_PREFIX + text
    raise errors.BridgeError(errors.INVALID_CONFIGURATION, f"unknown tool {tool!r}", code="bad_tool")


@dataclass
class _Ctx:
    tool: str
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=state.utc_now)
    t0: float = field(default_factory=time.monotonic)
    route: dict[str, str] | None = None
    repo_key: str | None = None
    session: str | None = None
    thread_id: str | None = None
    resumed: bool | None = None
    queue_ms: int | None = None
    run_ms: int | None = None
    outcome: str = "error"
    error_category: str | None = None
    error_code: str | None = None
    inference_may_have_started: bool = False
    exit_code: int | None = None
    usage: dict[str, int | None] = field(default_factory=telemetry.empty_usage)
    events: dict[str, int] | None = None
    prompt_chars: int | None = None
    answer_chars: int | None = None
    cli_version: str | None = None
    warnings: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "started_at": self.started_at,
            "ended_at": state.utc_now(),
            "route": self.route,
            "session": {
                "name": self.session,
                "repo_key": self.repo_key,
                "thread_id": self.thread_id,
                "resumed": self.resumed,
            },
            "queue_ms": self.queue_ms,
            "run_ms": self.run_ms,
            "outcome": self.outcome,
            "error_category": self.error_category,
            "error_code": self.error_code,
            "inference_may_have_started": self.inference_may_have_started,
            "exit_code": self.exit_code,
            "usage": self.usage,
            "events": self.events,
            "prompt_chars": self.prompt_chars,
            "answer_chars": self.answer_chars,
            "cli_version": self.cli_version,
            "warnings": self.warnings,
        }


class ArchitectService:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ
        self._cache: dict[tuple[str, str], tuple[dict[str, list[str]], set[str]]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def execute(self, tool: str, text: str, cancel: threading.Event | None = None) -> str:
        cancel = cancel or threading.Event()
        ctx = _Ctx(tool=tool)
        try:
            answer = self._execute(ctx, tool, text, cancel)
            ctx.outcome = "success"
            return answer
        except errors.BridgeError as exc:
            ctx.error_category, ctx.error_code = exc.category, exc.code
            ctx.inference_may_have_started = ctx.inference_may_have_started or exc.inference_may_have_started
            ctx.outcome = {errors.TIMEOUT: "timeout", errors.CANCELLED: "cancelled"}.get(exc.category, "error")
            raise
        except BaseException as exc:  # noqa: BLE001 - includes cancellation of the worker
            ctx.error_category, ctx.error_code = errors.INTERNAL, exc.__class__.__name__
            ctx.outcome = "error"
            if isinstance(exc, Exception):
                raise errors.BridgeError(
                    errors.INTERNAL,
                    f"Unexpected bridge failure ({exc.__class__.__name__}).",
                    code=exc.__class__.__name__,
                    inference_may_have_started=ctx.inference_may_have_started,
                    recovery="Check the MCP server's stderr log; no automatic retry was made.",
                ) from exc
            raise
        finally:
            self._emit(ctx)

    # ----------------------------------------------------------------- internals
    def _telemetry_dir(self) -> Path:
        """State dir for telemetry; never inside a repository or the v1 state dir."""
        home = Path(self._env.get("USERPROFILE") or self._env.get("HOME") or Path.home())
        chosen = config.default_state_dir(self._env)
        try:
            raw_root = self._env.get("CLAUDE_PROJECT_DIR") or os.getcwd()
            real = Path(os.path.realpath(raw_root))
            state.validate_state_dir(chosen, real if real.is_dir() else None, home)
            return chosen
        except Exception:  # noqa: BLE001 - invalid override: use the safe default location
            return home / config.V2_STATE_DIRNAME

    def _emit(self, ctx: _Ctx) -> None:
        """Write the single terminal record.  Never raises, never retries inference."""
        try:
            telemetry.Telemetry(self._telemetry_dir()).write(ctx.record())
        except Exception as exc:  # noqa: BLE001
            log.warning("telemetry emit failed: %s", exc.__class__.__name__)

    def _cached_cli_facts(self, cli: codexcli.Cli, version: str, env: Mapping[str, str]):
        key = (cli.display, version)
        with self._cache_lock:
            hit = self._cache.get(key)
        if hit:
            return hit
        facts = (codexcli.load_catalog(cli, env), codexcli.load_features(cli, env))
        with self._cache_lock:
            self._cache[key] = facts
        return facts

    def _execute(self, ctx: _Ctx, tool: str, text: str, cancel: threading.Event) -> str:
        # 1. input ---------------------------------------------------------
        if not isinstance(text, str) or not text.strip():
            raise errors.InputError("A non-empty question is required", code="empty_input")
        prompt = compose_prompt(tool, text)
        ctx.prompt_chars = len(prompt)
        if len(prompt) > config.MAX_PROMPT_CHARS:
            raise errors.InputError(
                "Question too long; pass references to repo files instead", code="input_too_long"
            )

        # 2. configuration -------------------------------------------------
        settings = config.load_settings(self._env)
        route = config.resolve_route(tool, self._env)
        ctx.route = route.public()
        ctx.session = settings.session

        raw_root = self._env.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        root, repo_key = state.canonical_repo(raw_root)
        ctx.repo_key = repo_key
        home = Path(self._env.get("USERPROFILE") or self._env.get("HOME") or Path.home())
        state.validate_state_dir(settings.state_dir, root, home)
        registry = state.Registry(settings.state_dir)
        registry.ensure_dirs()

        ident = {
            "repo_key": repo_key,
            "repo_path": str(root),
            "model": route.model,
            "session": settings.session,
        }
        key = state.session_key(repo_key, route.model, settings.session)

        # 3. queue (cross-process, per repo/model/session) ------------------
        queued = time.monotonic()
        lock = registry.session_lock(key)
        try:
            lock.acquire(settings.queue_timeout_s, cancel)
        except state.LockTimeout:
            ctx.queue_ms = int((time.monotonic() - queued) * 1000)
            raise errors.BridgeError(
                errors.QUEUE_TIMEOUT,
                f"Another call on this repository/model/session was still running after "
                f"{settings.queue_timeout_s}s.",
                code="queue_timeout",
                recovery="Retry later, or use a separate named session (GPT_ARCHITECT_SESSION).",
            ) from None
        except state.LockCancelled:
            ctx.queue_ms = int((time.monotonic() - queued) * 1000)
            raise errors.BridgeError(errors.CANCELLED, "Cancelled while queued.", code="cancelled_queued") from None
        ctx.queue_ms = int((time.monotonic() - queued) * 1000)

        try:
            answer = self._locked_call(ctx, settings, route, root, registry, key, ident, prompt, cancel)
        finally:
            lock.release()
        return answer

    def _locked_call(self, ctx, settings, route, root, registry, key, ident, prompt, cancel) -> str:
        entry = registry.get(key, ident)  # raises session_corrupt / session_mismatch, never replaces
        existing = entry.get("thread_id") if entry else None
        ctx.resumed = existing is not None
        ctx.thread_id = existing

        env = codexcli.child_env(self._env)
        cli = codexcli.resolve_cli(settings.codex_path_override, self._env)
        version = codexcli.get_version(cli, env)
        ctx.cli_version = version
        codexcli.check_compat(version, settings.allow_untested_cli)
        codexcli.check_auth(cli, env)
        catalog, features = self._cached_cli_facts(cli, version, env)
        config.validate_route_against_catalog(route, catalog)
        audit = codexcli.capability_audit(cli, env, root, settings.mcp_allow, features)
        if settings.sandbox_preflight:
            result = codexcli.sandbox_preflight(cli, env, root, audit, settings.windows_sandbox)
            if result.get("skipped"):
                ctx.warnings.append(f"sandbox_preflight_skipped:{result['skipped']}")
        else:
            ctx.warnings.append("sandbox_preflight_skipped:disabled")

        if cancel.is_set():
            raise errors.BridgeError(errors.CANCELLED, "Cancelled before inference.", code="cancelled_early")

        with tempfile.TemporaryDirectory(prefix="gpt_architect_v2_") as tmp:
            answer_file = str(Path(tmp) / "answer.txt")
            argv = codexcli.build_argv(
                cli,
                model=route.model,
                effort=route.effort,
                output_file=answer_file,
                audit=audit,
                resume_thread=existing,
                windows_sandbox=settings.windows_sandbox,
            )
            ctx.inference_may_have_started = True  # from here on: never replay
            result = runner.run_turn(argv, prompt, env, str(root), settings.timeout_s, cancel)
            ctx.run_ms = int(result.duration_s * 1000)
            ctx.exit_code = result.returncode
            ev = result.events
            ctx.usage = dict(ev.usage) if ev.turn_completed else telemetry.empty_usage()
            ctx.events = {
                "total": ev.total,
                "unknown_types": ev.unknown_types,
                "non_json_lines": ev.non_json,
                "oversize_lines": ev.oversize,
                "errors_before_completion": ev.errors_before_completion,
                "errors_after_completion": ev.errors_after_completion,
            }
            if ev.thread_id:
                ctx.thread_id = ev.thread_id
            if not result.cleanup_ok:
                ctx.warnings.append("child_cleanup_incomplete")

            answer = self._verify(result, existing, answer_file, settings.timeout_s)

        # 4. persist pointer; failure here must not discard or replay the answer
        try:
            registry.bind_thread(key, ident, ctx.thread_id or "", version)
        except errors.BridgeError as exc:
            ctx.warnings.append(f"state_not_saved:{exc.code}")
            log.warning("session pointer not saved: %s", exc.code)
            answer += (
                "\n\n[bridge-warning] The answer is valid but the session pointer could not be saved "
                f"({exc.code}); the next call may start a new GPT session. Run "
                "`bridge_admin.py status` to inspect state."
            )
        ctx.answer_chars = len(answer)
        return answer

    @staticmethod
    def _verify(result: runner.RunResult, existing: str | None, answer_file: str, timeout_s: int) -> str:
        ev = result.events
        detail = "; ".join(ev.messages) or result.stderr_tail.strip()[-1000:]
        detail = detail[:1400]

        if result.cancelled:
            raise errors.BridgeError(
                errors.CANCELLED,
                "The call was cancelled; the Codex process tree was terminated.",
                code="cancelled",
                inference_may_have_started=True,
                recovery="Session state is unchanged and usable; the cancelled turn was not retried.",
            )
        if result.timed_out:
            raise errors.BridgeError(
                errors.TIMEOUT,
                f"Codex did not finish within {timeout_s}s; the process tree was terminated.",
                code="timeout",
                inference_may_have_started=True,
                recovery=(
                    "Session state is unchanged. Narrow the question or raise GPT_ARCHITECT_TIMEOUT; "
                    "the bridge does not retry."
                ),
            )
        haystack = detail
        if result.returncode != 0 or ev.turn_failed:
            if _RATE_LIMIT.search(haystack):
                raise errors.BridgeError(
                    errors.RATE_LIMITED,
                    f"Codex reported a rate/usage limit (exit {result.returncode}): {detail}",
                    code="rate_limited",
                    inference_may_have_started=True,
                    recovery="Wait for the allowance window to reset; session state is intact. No retry was made.",
                )
            if _AUTH_RUNTIME.search(haystack):
                raise errors.BridgeError(
                    errors.AUTHENTICATION,
                    f"Codex rejected the stored login during the call (exit {result.returncode}).",
                    code="auth_runtime",
                    inference_may_have_started=True,
                    recovery="Run `codex login status` / `codex login` (ChatGPT) yourself; the bridge never logs in.",
                )
            if existing and _SESSION_MISSING.search(haystack):
                raise errors.BridgeError(
                    errors.SESSION_MISMATCH,
                    "Codex could not find the saved session; the bridge did not start a replacement.",
                    code="session_missing",
                    inference_may_have_started=True,
                    recovery=(
                        "Check Codex history. If the thread is gone, run `bridge_admin.py reset --yes` to "
                        "retire the pointer explicitly; the next call then starts a new session."
                    ),
                )
            raise errors.BridgeError(
                errors.SUBPROCESS_FAILED,
                f"Codex failed (exit {result.returncode}): {detail or 'no diagnostic output'}",
                code="nonzero_exit" if result.returncode != 0 else "turn_failed",
                inference_may_have_started=True,
                recovery="Session state is unchanged. Re-run manually after fixing the cause; no retry was made.",
            )
        if not ev.turn_completed:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex exited 0 but never reported a completed turn.",
                code="no_turn_completed",
                inference_may_have_started=True,
                recovery="The answer was discarded and state is unchanged. Upgrade-check the CLI event format.",
            )
        if ev.errors_after_completion:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                f"Codex reported an error after the turn completed: {detail}",
                code="error_after_completion",
                inference_may_have_started=True,
            )
        if ev.thread_conflict or not ev.thread_id:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex did not report a single, stable thread ID; the session was not saved.",
                code="no_thread_id" if not ev.thread_id else "thread_conflict",
                inference_may_have_started=True,
            )
        if existing and ev.thread_id != existing:
            raise errors.BridgeError(
                errors.SESSION_MISMATCH,
                "Codex resumed a different thread than the one the bridge saved; the answer was discarded.",
                code="resumed_different_thread",
                inference_may_have_started=True,
                recovery=(
                    "Check Codex history for an unexpected new thread, then decide: "
                    "`bridge_admin.py reset --yes` retires the old pointer (history is kept)."
                ),
            )
        path = Path(answer_file)
        if not path.is_file():
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex completed but wrote no final-answer file.",
                code="no_answer_file",
                inference_may_have_started=True,
            )
        answer = path.read_text(encoding="utf-8", errors="replace").strip()
        if not answer:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex completed but the final answer is empty.",
                code="empty_answer",
                inference_may_have_started=True,
            )
        return answer
