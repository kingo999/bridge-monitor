"""One architect call, end to end.

Flow (every step before ``run_turn`` is non-inference and can fail cleanly):

    validate input -> resolve route -> canonical repo + state dir -> maintenance gate
    -> session lock -> registry entry -> session-health gates (pending / in-flight)
    -> CLI version (exact) -> auth -> catalog validation -> capability audit
    -> sandbox preflight (readiness only) -> repo fingerprint (before)
    -> in-flight marker -> run (job-owned process tree) -> repo fingerprint (after)
    -> termination reconciliation -> verify completion -> actual-turn infra check
    -> bind thread -> telemetry (exactly once, in ``finally``)

There is no retry anywhere: once ``run_turn`` has been entered the call is never replayed,
whatever happens to telemetry, state or process cleanup afterwards.

Outcome vocabulary (F5) - the bridge *pointer* and Codex *history* are different things:

    success                       answer returned, pointer saved, process tree reconciled
    degraded                      answer returned although the actual turn hit infrastructure
                                  failures (explicit opt-in mode only)
    success_partial_persistence   answer returned, pointer NOT saved; a pending marker blocks
                                  silent replacement threads until `adopt`/`reset`
    success_session_in_doubt      answer returned; process cleanup could not be confirmed, so the
                                  session is blocked until `reconcile`
    error / timeout / cancelled   no answer; pointer unchanged; Codex history MAY contain a
                                  partial or complete turn (thread id preserved)
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

from . import codexcli, config, errors, fingerprint, runner, state, telemetry

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
    final_outcome: str | None = None
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
    banners: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] | None = None
    process: dict[str, Any] | None = None
    infra: dict[str, Any] | None = None
    repo: dict[str, Any] | None = None
    pointer_state: str = "not_reached"   # not_reached | unchanged | bound | bind_failed | pending_reconcile
    history: str = "no_turn_started"     # no_turn_started | turn_may_exist
    session_state: str = "ready"         # ready | in_doubt | pending_reconcile

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
            "capabilities": self.capabilities,
            "process": self.process,
            "infra": self.infra,
            "repo": self.repo,
            "pointer_state": self.pointer_state,
            "history": self.history,
            "session_state": self.session_state,
        }


def _termination_words(termination: str | None) -> str:
    return {
        "confirmed": "owned process tree termination CONFIRMED (0 live members)",
        "unconfirmed": "termination was ATTEMPTED but is NOT CONFIRMED",
        "not_needed": "no cleanup was needed",
        None: "no process was started",
    }.get(termination, str(termination))


def _history_words(thread_id: str | None, may_exist: bool) -> str:
    if not may_exist:
        return "No model turn was started; Codex history is unaffected."
    where = f"thread {thread_id}" if thread_id else "a Codex thread (id not observed)"
    return f"Codex history MAY contain a partial or complete turn in {where}; it was not removed."


class ArchitectService:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ
        self._cache: dict[tuple[str, str], tuple[dict[str, list[str]], dict[str, bool]]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def execute(self, tool: str, text: str, cancel: threading.Event | None = None) -> str:
        cancel = cancel or threading.Event()
        ctx = _Ctx(tool=tool)
        try:
            answer = self._execute(ctx, tool, text, cancel)
            ctx.outcome = ctx.final_outcome or "success"
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
                    recovery="Check the MCP server's stderr log; no automatic retry was made. "
                             + _history_words(ctx.thread_id, ctx.inference_may_have_started),
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
        registry.assert_not_in_maintenance()

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

    # ------------------------------------------------------------- session health
    def _check_session_health(self, ctx: _Ctx, registry: state.Registry, key: str) -> None:
        """Refuse reuse while a previous call left the session unreconciled (F3/F5)."""
        pending = registry.read_marker("pending", key)
        if pending is not None:
            ctx.session_state = "pending_reconcile"
            raise errors.BridgeError(
                errors.SESSION_IN_DOUBT,
                "A previous call produced Codex thread "
                f"{pending.get('thread_id', '(unknown)')} but the bridge pointer could not be saved "
                f"({pending.get('reason', 'unknown reason')}). The bridge will not silently start "
                "another thread for this session.",
                code="pending_persistence",
                recovery=(
                    f"Attach it: `bridge_admin.py adopt --thread {pending.get('thread_id', '<id>')} --yes` "
                    "(same repository/model/session selection), or discard the marker with "
                    "`bridge_admin.py reset --yes`. The thread stays in Codex history either way."
                ),
            )
        marker = registry.read_marker("inflight", key)
        if marker is None:
            return
        tid = marker.get("thread_id") or "(not observed)"
        if marker.get("state") == "uncertain":
            ctx.session_state = "in_doubt"
            raise errors.BridgeError(
                errors.SESSION_IN_DOUBT,
                "A previous call's process cleanup was attempted but NOT confirmed "
                f"(thread {tid}; ownership {marker.get('ownership', 'unknown')}). The session is blocked so "
                "a second turn cannot run against a possibly still-active one.",
                code="uncertain_termination",
                recovery=(
                    "Verify no `codex` / `node_repl` processes of that call remain (the bridge never kills "
                    "processes it does not own), then `bridge_admin.py reconcile --yes`."
                ),
            )
        # state == running while we hold the session lock: the owning call is gone
        if marker.get("ownership") == "job_object":
            registry.clear_marker("inflight", key)
            registry.journal.append("auto_reconciled_hard_termination", call_id=marker.get("call_id"),
                                    thread_id=marker.get("thread_id"), key_hash=state.key_hash(key))
            ctx.warnings.append("previous_call_hard_terminated_job_closed")
            ctx.banners.append(
                "A previous call was hard-terminated. Its job-owned process tree was killed by the OS "
                "when the bridge exited; commands started outside that job (Windows sandbox service) "
                f"cannot be confirmed. Codex history may hold a partial turn in thread {tid}."
            )
            return
        ctx.session_state = "in_doubt"
        raise errors.BridgeError(
            errors.SESSION_IN_DOUBT,
            "A previous call did not finish and its process ownership was not established "
            f"(thread {tid}). Termination of its processes cannot be assumed.",
            code="stale_inflight_unowned",
            recovery="Verify no stray `codex` processes remain, then `bridge_admin.py reconcile --yes`.",
        )

    # ----------------------------------------------------------------- the call
    def _locked_call(self, ctx, settings, route, root, registry, key, ident, prompt, cancel) -> str:
        registry.assert_not_in_maintenance()  # may have started while we queued
        entry = registry.get(key, ident)      # raises session_corrupt / registry_lost / mismatch
        existing = entry.get("thread_id") if entry else None
        ctx.resumed = existing is not None
        ctx.thread_id = existing
        ctx.pointer_state = "unchanged"
        self._check_session_health(ctx, registry, key)

        env = codexcli.child_env(self._env)
        cli = codexcli.resolve_cli(settings.codex_path_override, self._env)
        version = codexcli.get_version(cli, env)
        ctx.cli_version = version
        compat_warning = codexcli.check_compat(version, settings.allow_untested_cli)
        if compat_warning:
            ctx.warnings.append(compat_warning)
            ctx.banners.append(
                f"Codex CLI {version} is outside the supported versions "
                f"({', '.join(codexcli.SUPPORTED_CLI_VERSIONS)}); its behaviour is unverified."
            )
        if settings.windows_sandbox:
            # an explicit backend override is a configuration the attack tests have not covered
            ctx.warnings.append(f"windows_sandbox_override:{settings.windows_sandbox}")
            ctx.banners.append(
                f"The Windows sandbox backend is overridden to '{settings.windows_sandbox}' "
                "(GPT_ARCHITECT_WINDOWS_SANDBOX). That configuration has no apply_patch/shell attack-test "
                "evidence for this bridge; the recorded evidence is for the user's own Codex config."
            )
        codexcli.check_auth(cli, env)
        catalog, features = self._cached_cli_facts(cli, version, env)
        config.validate_route_against_catalog(route, catalog)
        audit = codexcli.capability_audit(cli, env, root, settings.mcp_allow, features)
        ctx.capabilities = audit.telemetry_summary()
        if audit.allowed_servers:
            ctx.warnings.append("mcp_allow_list_active:" + ",".join(audit.allowed_servers))
            ctx.banners.append(
                "MCP servers allowed through the capability policy by configuration "
                f"(GPT_ARCHITECT_MCP_ALLOW): {', '.join(audit.allowed_servers)}. They are NOT contained by "
                "the read-only sandbox."
            )
        if settings.sandbox_preflight:
            result = codexcli.sandbox_preflight(cli, env, root, audit, settings.windows_sandbox)
            if result.get("skipped"):
                ctx.warnings.append(f"sandbox_preflight_skipped:{result['skipped']}")
        else:
            ctx.warnings.append("sandbox_preflight_skipped:disabled")

        if cancel.is_set():
            raise errors.BridgeError(errors.CANCELLED, "Cancelled before inference.", code="cancelled_early")

        fp_before = fingerprint.repo_fingerprint(root, env)
        key_h = state.key_hash(key)

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
            marker: dict[str, Any] = {
                "state": "running", "call_id": ctx.call_id, "owner_pid": os.getpid(),
                "started_at": state.utc_now(), "resumed": existing is not None,
                "thread_id": existing, "ownership": "pending", "model": route.model,
            }
            ctx.inference_may_have_started = True  # from here on: never replay
            ctx.history = "turn_may_exist"
            registry.write_marker("inflight", key, marker)
            registry.journal.append("turn_started", call_id=ctx.call_id, key_hash=key_h,
                                    resumed=existing is not None, thread_id=existing)

            def on_started(info: dict[str, Any]) -> None:
                marker.update(child_pid=info.get("child_pid"), ownership=info.get("ownership"))
                registry.write_marker("inflight", key, marker)

            def on_thread(thread_id: str) -> None:
                marker["thread_id"] = thread_id
                registry.write_marker("inflight", key, marker)

            result = runner.run_turn(argv, prompt, env, str(root), settings.timeout_s, cancel,
                                     on_started=on_started, on_thread=on_thread)
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
            ctx.infra = {"command_items": ev.command_items, "failed_items": ev.infra_failed_items,
                         "kinds": dict(ev.infra_kinds)}
            ctx.process = {
                "ownership": result.ownership, "ownership_error": result.ownership_error,
                "termination": result.termination, "reaped_descendants": result.reaped_descendants,
                "active_after": result.active_after,
            }
            if ev.thread_id:
                ctx.thread_id = ev.thread_id

            # --- reconcile process ownership BEFORE anything can raise -----------------
            fp_after = fingerprint.repo_fingerprint(root, env)
            ctx.repo = fingerprint.compare(fp_before, fp_after)
            in_doubt = result.termination == "unconfirmed"
            if in_doubt:
                marker.update(state="uncertain", termination="attempted_unconfirmed",
                              thread_id=ev.thread_id or marker.get("thread_id"), since=state.utc_now())
                registry.write_marker("inflight", key, marker)
                registry.journal.append("termination_unconfirmed", call_id=ctx.call_id, key_hash=key_h,
                                        thread_id=ev.thread_id, ownership=result.ownership,
                                        active_after=result.active_after)
                ctx.session_state = "in_doubt"
                ctx.warnings.append("child_termination_unconfirmed")
            else:
                registry.clear_marker("inflight", key)
                registry.journal.append("turn_finished", call_id=ctx.call_id, key_hash=key_h,
                                        thread_id=ev.thread_id, termination=result.termination,
                                        reaped_descendants=result.reaped_descendants,
                                        timed_out=result.timed_out, cancelled=result.cancelled,
                                        exit_code=result.returncode)
                if result.reaped_descendants:
                    ctx.warnings.append(f"reaped_descendants:{result.reaped_descendants}")
            if result.ownership == "none":
                ctx.warnings.append("process_ownership_unavailable")

            try:
                answer = self._verify(result, existing, answer_file, settings.timeout_s)
            except errors.BridgeError as exc:
                if ev.thread_id and existing is None:
                    registry.journal.append("unbound_thread", call_id=ctx.call_id, key_hash=key_h,
                                            thread_id=ev.thread_id, reason=exc.code)
                if in_doubt:
                    exc.args = (exc.args[0] + "\nProcess cleanup: attempted but NOT confirmed; the session "
                                "is blocked until `bridge_admin.py reconcile --yes`.",)
                raise

        # --- F1: the actual turn must not hide infrastructure failures ----------------
        infra_banner: str | None = None
        if ev.infra_failed_items:
            kinds = sorted(ev.infra_kinds)
            code = ("turn_sandbox_setup_failed" if "sandbox_setup" in kinds else
                    "turn_process_creation_failed" if "process_creation" in kinds else
                    "turn_repo_access_failed")
            detail = (f"{ev.infra_failed_items} of {ev.command_items} command/tool item(s) in the actual turn "
                      f"reported infrastructure failures ({', '.join(kinds)}).")
            if settings.on_infra_failure == "error":
                if ev.thread_id and existing is None:
                    registry.journal.append("unbound_thread", call_id=ctx.call_id, key_hash=key_h,
                                            thread_id=ev.thread_id, reason=code)
                raise errors.BridgeError(
                    errors.SANDBOX_UNAVAILABLE,
                    "The turn completed with an answer, but the answer cannot be trusted as a repository "
                    f"review: {detail} The answer was withheld.",
                    code=code,
                    inference_may_have_started=True,
                    recovery=(
                        "Fix the sandbox/repository access problem (preflight is only a readiness check "
                        "and can differ from the real turn; see README 'Sandbox preflight'). The bridge "
                        "pointer is unchanged; " + _history_words(ev.thread_id, True) + " No retry was "
                        "made. To receive such answers with a banner instead, set "
                        "GPT_ARCHITECT_ON_TURN_INFRA_FAILURE=degraded."
                    ),
                )
            ctx.warnings.append(f"turn_infra_failures:{ev.infra_failed_items}")
            infra_banner = (f"DEGRADED RESULT: {detail} The review below may not be grounded in the "
                            "repository. Do not treat it as a completed review.")

        # --- persistence (F5): distinguish pointer from history -------------------------
        persistence_banner: str | None = None
        try:
            registry.bind_thread(key, ident, ctx.thread_id or "", version)
            ctx.pointer_state = "bound"
        except errors.BridgeError as exc:
            ctx.warnings.append(f"state_not_saved:{exc.code}")
            log.warning("session pointer not saved: %s", exc.code)
            if existing is not None and ctx.thread_id == existing:
                ctx.pointer_state = "unchanged"  # pointer already references this thread
                persistence_banner = (
                    "The session pointer already references this thread, but the turn counter could not "
                    f"be updated ({exc.code})."
                )
            else:
                wrote = registry.write_marker("pending", key, {
                    "thread_id": ctx.thread_id, "call_id": ctx.call_id, "reason": exc.code,
                    "since": state.utc_now(), "model": route.model, "session": ident["session"],
                    "repo_key": ident["repo_key"],
                })
                registry.journal.append("pointer_not_saved", call_id=ctx.call_id, key_hash=key_h,
                                        thread_id=ctx.thread_id, reason=exc.code, marker_written=wrote)
                ctx.pointer_state = "pending_reconcile" if wrote else "bind_failed"
                ctx.session_state = "pending_reconcile" if wrote else ctx.session_state
                ctx.final_outcome = "success_partial_persistence"
                persistence_banner = (
                    "PARTIAL SUCCESS: the answer is valid, but the bridge could not save the session "
                    f"pointer ({exc.code}). Codex thread {ctx.thread_id} exists in Codex history and "
                    + ("a pending marker now blocks silent replacement threads; attach it with "
                       f"`bridge_admin.py adopt --thread {ctx.thread_id} --yes` or discard with `reset --yes`."
                       if wrote else
                       "NO pending marker could be written either, so the next call may start another "
                       "thread: record this thread id and adopt it manually.")
                )

        # --- compose returned warnings -------------------------------------------------
        banners: list[str] = []
        if infra_banner:
            ctx.final_outcome = "degraded"
            banners.append(infra_banner)
        if persistence_banner:
            banners.append(persistence_banner)
        if in_doubt:
            ctx.final_outcome = ctx.final_outcome or "success_session_in_doubt"
            banners.append(
                "Process cleanup for this call was attempted but is NOT confirmed (ownership: "
                f"{result.ownership}); this session is blocked until `bridge_admin.py reconcile --yes`."
            )
        if ctx.repo and ctx.repo["changed"]:
            ctx.warnings.append("repo_changed_during_call")
            banners.append(
                "The repository CHANGED while GPT was reviewing it"
                + (" (HEAD moved)" if ctx.repo["head_changed"] else " (working tree)")
                + ". Findings may describe a state that no longer exists; re-run the review if the "
                  "change matters. (Session locks do not freeze the repository.)"
            )
        elif ctx.repo and not ctx.repo["comparable"]:
            ctx.warnings.append(f"repo_fingerprint_unavailable:{ctx.repo['reason']}")
        banners.extend(ctx.banners)
        if banners:
            answer += "".join(f"\n\n[bridge-warning] {b}" for b in banners)
        ctx.answer_chars = len(answer)
        return answer

    # ----------------------------------------------------------------- verification
    @staticmethod
    def _verify(result: runner.RunResult, existing: str | None, answer_file: str, timeout_s: int) -> str:
        ev = result.events
        detail = "; ".join(ev.messages) or result.stderr_tail.strip()[-1000:]
        detail = detail[:1400]
        tail = (f" Process cleanup: {_termination_words(result.termination)}. Bridge pointer: unchanged. "
                f"{_history_words(ev.thread_id or existing, True)}")

        if result.cancelled:
            raise errors.BridgeError(
                errors.CANCELLED,
                "The call was cancelled." + tail,
                code="cancelled",
                inference_may_have_started=True,
                recovery="The cancelled turn was not retried.",
            )
        if result.timed_out:
            raise errors.BridgeError(
                errors.TIMEOUT,
                f"Codex did not finish within {timeout_s}s." + tail,
                code="timeout",
                inference_may_have_started=True,
                recovery="Narrow the question or raise GPT_ARCHITECT_TIMEOUT; the bridge does not retry.",
            )
        haystack = detail
        if result.returncode != 0 or ev.turn_failed:
            infra = (f" Actual-turn infrastructure failures seen: {', '.join(sorted(ev.infra_kinds))}."
                     if ev.infra_kinds else "")
            if _RATE_LIMIT.search(haystack):
                raise errors.BridgeError(
                    errors.RATE_LIMITED,
                    f"Codex reported a rate/usage limit (exit {result.returncode}): {detail}" + tail,
                    code="rate_limited",
                    inference_may_have_started=True,
                    recovery="Wait for the allowance window to reset. No retry was made.",
                )
            if _AUTH_RUNTIME.search(haystack):
                raise errors.BridgeError(
                    errors.AUTHENTICATION,
                    f"Codex rejected the stored login during the call (exit {result.returncode})." + tail,
                    code="auth_runtime",
                    inference_may_have_started=True,
                    recovery="Run `codex login status` / `codex login` (ChatGPT) yourself; the bridge never logs in.",
                )
            if existing and _SESSION_MISSING.search(haystack):
                raise errors.BridgeError(
                    errors.SESSION_MISMATCH,
                    "Codex could not find the saved session; the bridge did not start a replacement."
                    + tail,
                    code="session_missing",
                    inference_may_have_started=True,
                    recovery=(
                        "Check Codex history. If the thread is gone, run `bridge_admin.py reset --yes` to "
                        "retire the pointer explicitly; the next call then starts a new session."
                    ),
                )
            raise errors.BridgeError(
                errors.SUBPROCESS_FAILED,
                f"Codex failed (exit {result.returncode}): {detail or 'no diagnostic output'}." + infra + tail,
                code="nonzero_exit" if result.returncode != 0 else "turn_failed",
                inference_may_have_started=True,
                recovery="No retry was made; fix the cause and re-run manually.",
            )
        if not ev.turn_completed:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex exited 0 but never reported a completed turn." + tail,
                code="no_turn_completed",
                inference_may_have_started=True,
                recovery="The answer was discarded. Check the CLI event format against the supported version.",
            )
        if ev.errors_after_completion:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                f"Codex reported an error after the turn completed: {detail}" + tail,
                code="error_after_completion",
                inference_may_have_started=True,
            )
        if ev.thread_conflict or not ev.thread_id:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex did not report a single, stable thread ID; the session was not saved." + tail,
                code="no_thread_id" if not ev.thread_id else "thread_conflict",
                inference_may_have_started=True,
            )
        if existing and ev.thread_id != existing:
            raise errors.BridgeError(
                errors.SESSION_MISMATCH,
                "Codex resumed a different thread than the one the bridge saved; the answer was discarded."
                + tail,
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
                "Codex completed but wrote no final-answer file." + tail,
                code="no_answer_file",
                inference_may_have_started=True,
            )
        answer = path.read_text(encoding="utf-8", errors="replace").strip()
        if not answer:
            raise errors.BridgeError(
                errors.OUTPUT_INCOMPLETE,
                "Codex completed but the final answer is empty." + tail,
                code="empty_answer",
                inference_may_have_started=True,
            )
        return answer
