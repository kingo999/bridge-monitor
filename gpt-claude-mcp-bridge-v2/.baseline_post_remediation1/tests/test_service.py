"""End-to-end service behaviour against the scripted fake Codex CLI.

Every test starts real child processes (the fake), exercises real locks and
files in a temp world, and makes zero subscription / model calls.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import WorldCase, pid_alive, wait_for

from bridge import config, errors, state, telemetry
from bridge.service import BASE_INSTRUCTIONS, compose_prompt

CONSULT, REVIEW = config.CONSULT, config.REVIEW


def run_expect_error(case: WorldCase, svc, tool=CONSULT, text="q") -> errors.BridgeError:
    with case.assertRaises(errors.BridgeError) as cm:
        svc.execute(tool, text)
    return cm.exception


class HappyPath(WorldCase):
    def test_fresh_then_resumed_with_continuity_and_telemetry(self):
        svc = self.service()
        self.assertEqual(svc.execute(CONSULT, "first"), "FAKE ANSWER")
        self.assertEqual(svc.execute(REVIEW, "second"), "FAKE ANSWER")
        # a brand-new service object == process restart
        self.assertEqual(self.service().execute(CONSULT, "third"), "FAKE ANSWER")

        runs = self.w.exec_entries()
        self.assertEqual([r["kind"] for r in runs], ["exec_fresh", "exec_resume", "exec_resume"])
        thread = self.w.registry()["sessions"]
        self.assertEqual(len(thread), 1)
        tid = next(iter(thread.values()))["thread_id"]
        for r in runs[1:]:
            self.assertEqual(r["argv"][1:3], ["resume", tid])
        for r in runs:
            joined = " ".join(r["argv"])
            self.assertIn('sandbox_mode="read-only"', joined)
            self.assertIn('approval_policy="never"', joined)
            self.assertIn("--model gpt-6.1-sol", joined)
            self.assertIn('model_reasoning_effort="high"', joined)
            self.assertNotIn("dangerously", joined)
            self.assertEqual(Path(r["cwd"]).resolve(), self.w.repo.resolve())
        self.assertEqual(next(iter(thread.values()))["turns"], 3)

        recs = self.w.telemetry()
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["tool"] for r in recs], [CONSULT, REVIEW, CONSULT])
        self.assertEqual([r["session"]["resumed"] for r in recs], [False, True, True])
        for r in recs:
            self.assertEqual(r["outcome"], "success")
            self.assertEqual(r["usage"]["input_tokens"], 120)
            self.assertEqual(r["usage"]["cached_input_tokens"], 100)
            self.assertIsNone(r["usage"]["total_tokens"], "absent field is null, never 0")
            self.assertIsInstance(r["queue_ms"], int)
            self.assertIsInstance(r["run_ms"], int)
            self.assertEqual(r["route"]["model"], "gpt-6.1-sol")
            self.assertEqual(r["route"]["effort"], "high")
            self.assertEqual(r["session"]["thread_id"], tid)
            self.assertEqual(r["cli_version"], "0.162.1")

    def test_prompt_composition_matches_v1(self):
        self.assertTrue(compose_prompt(CONSULT, "Q").startswith(BASE_INSTRUCTIONS + "\nArchitecture consultation:\nQ"))
        self.assertIn("Specific focus:\nFOCUS", compose_prompt(REVIEW, "FOCUS"))
        v1 = Path(r"C:\AI\gpt-claude-mcp-bridge\architect_bridge.py")
        if v1.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("v1_for_compare", v1)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertEqual(BASE_INSTRUCTIONS, mod.BASE_INSTRUCTIONS)

    def test_per_tool_routing_and_model_separated_sessions(self):
        svc = self.service(GPT_ARCHITECT_MODEL_REVIEW="gpt-6-astra", GPT_ARCHITECT_EFFORT_REVIEW="xhigh")
        svc.execute(CONSULT, "a")   # sol, fresh
        svc.execute(REVIEW, "b")    # astra, fresh (separate session)
        svc.execute(CONSULT, "c")   # sol, resume
        svc.execute(REVIEW, "d")    # astra, resume
        kinds = [r["kind"] for r in self.w.exec_entries()]
        self.assertEqual(kinds, ["exec_fresh", "exec_fresh", "exec_resume", "exec_resume"])
        models = [r["argv"][r["argv"].index("--model") + 1] for r in self.w.exec_entries()]
        self.assertEqual(models, ["gpt-6.1-sol", "gpt-6-astra", "gpt-6.1-sol", "gpt-6-astra"])
        efforts = [[a for a in r["argv"] if a.startswith("model_reasoning_effort")][0] for r in self.w.exec_entries()]
        self.assertEqual(efforts[1], 'model_reasoning_effort="xhigh"')
        self.assertEqual(efforts[3], 'model_reasoning_effort="xhigh"')
        self.assertEqual(efforts[2], 'model_reasoning_effort="high"')
        entries = self.w.registry()["sessions"].values()
        self.assertEqual(sorted(e["model"] for e in entries), ["gpt-6-astra", "gpt-6.1-sol"])
        self.assertEqual(len({e["thread_id"] for e in entries}), 2)

    def test_named_session_is_separate_from_default(self):
        self.service().execute(CONSULT, "a")
        self.service(GPT_ARCHITECT_SESSION="alt").execute(CONSULT, "b")
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh", "exec_fresh"])
        self.assertEqual(len(self.w.registry()["sessions"]), 2)

    def test_repository_isolation(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        other = self.w.tmp / "other_repo"
        (other / ".git").mkdir(parents=True)
        self.w.extra_env["CLAUDE_PROJECT_DIR"] = str(other)
        self.service().execute(CONSULT, "b")
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh", "exec_fresh"])
        self.assertEqual(len({e["repo_key"] for e in self.w.registry()["sessions"].values()}), 2)

    def test_events_with_unknown_types_and_garbage_lines_still_succeed(self):
        self.w.set(exec={"events": [
            "not json at all",
            '{"no_type": true}',
            {"type": "thread.started", "thread_id": "{thread}"},
            {"type": "future.event.kind", "x": 1},
            {"type": "item.updated", "item": {}},
            {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 6}},
        ]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["events"]["unknown_types"], 1)
        self.assertEqual(rec["events"]["non_json_lines"], 2)
        self.assertEqual(rec["usage"]["input_tokens"], 5)
        self.assertIsNone(rec["usage"]["reasoning_output_tokens"])

    def test_oversize_event_lines_are_skipped_and_capture_is_bounded(self):
        from bridge import runner

        giant = '{"type":"item.updated","blob":"' + "x" * (runner.MAX_LINE_BYTES * 2) + '"}'
        self.w.set(exec={"events": [
            {"type": "thread.started", "thread_id": "{thread}"},
            giant,
            {"type": "turn.completed", "usage": {"input_tokens": 1}},
        ]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["events"]["oversize_lines"], 1)
        self.assertEqual(rec["usage"]["input_tokens"], 1)
        self.assertLess(len(self.w.telemetry_text()), 2000)

    def test_huge_stderr_is_tail_bounded_in_errors_and_never_in_telemetry(self):
        self.w.set(exec={"exit": 2, "events": [], "stderr": "E" * 400_000 + "TAIL-MARKER"})
        exc = run_expect_error(self, self.service())
        self.assertLess(len(str(exc)), 3500)
        self.assertIn("TAIL-MARKER", str(exc))
        self.assertLess(len(self.w.telemetry_text()), 2000)

    def test_missing_usage_is_null_not_zero(self):
        self.w.set(exec={"usage": None})
        self.service().execute(CONSULT, "q")
        usage = self.w.telemetry()[0]["usage"]
        self.assertEqual(set(usage), set(telemetry.USAGE_FIELDS))
        self.assertTrue(all(v is None for v in usage.values()))

    def test_error_event_before_completion_is_tolerated_but_after_is_not(self):
        base = [{"type": "thread.started", "thread_id": "{thread}"}]
        self.w.set(exec={"events": [*base, {"type": "error", "message": "Reconnecting... 1/5"},
                                    {"type": "turn.completed"}]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(self.w.telemetry()[0]["events"]["errors_before_completion"], 1)

        self.setUp()
        self.w.set(exec={"events": [*base, {"type": "turn.completed"}, {"type": "error", "message": "late"}]})
        exc = run_expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.OUTPUT_INCOMPLETE, "error_after_completion"))


class FailureModes(WorldCase):
    def assertState_untouched(self):
        self.assertFalse((self.w.state / "registry.json").exists() and self.w.registry()["sessions"])

    def test_input_validation_happens_before_any_cli_use(self):
        svc = self.service()
        exc = run_expect_error(self, svc, text="   ")
        self.assertEqual((exc.category, exc.code), (errors.INVALID_INPUT, "empty_input"))
        self.assertIsInstance(exc, ValueError)
        exc = run_expect_error(self, svc, text="x" * 70_000)
        self.assertEqual(exc.code, "input_too_long")
        self.assertEqual(self.w.log_entries(), [])
        self.assertEqual([r["outcome"] for r in self.w.telemetry()], ["error", "error"])

    def test_unsupported_effort_and_model_rejected_before_inference(self):
        exc = run_expect_error(self, self.service(GPT_ARCHITECT_EFFORT="ultra-mega"))
        self.assertEqual(exc.code, "unsupported_effort")
        exc = run_expect_error(self, self.service(GPT_ARCHITECT_MODEL_REVIEW="gpt-9"), REVIEW)
        self.assertEqual(exc.code, "unknown_model")
        # no silent fallback: nothing was executed with any other model
        self.assertEqual(self.w.exec_entries(), [])

    def test_missing_cli(self):
        env = self.w.env(GPT_ARCHITECT_CODEX_PATH=str(self.w.tmp / "nope.py"))
        from bridge.service import ArchitectService

        with self.assertRaises(errors.BridgeError) as cm:
            ArchitectService(env).execute(CONSULT, "q")
        self.assertEqual(cm.exception.category, errors.CLI_MISSING)
        self.assertEqual(self.w.telemetry()[0]["error_category"], errors.CLI_MISSING)

    def test_untested_cli_version_blocked_unless_opted_in(self):
        self.w.set(version="0.163.0")
        exc = run_expect_error(self, self.service())
        self.assertEqual(exc.category, errors.CLI_INCOMPATIBLE)
        self.assertEqual(self.w.exec_entries(), [])
        # an exact-version gate: a sibling release is not presumed equivalent
        self.w.set(version="0.162.9")
        self.assertEqual(run_expect_error(self, self.service()).code, "untested_cli")
        answer = self.service(GPT_ARCHITECT_ALLOW_UNTESTED_CLI="1").execute(CONSULT, "q")
        self.assertTrue(answer.startswith("FAKE ANSWER"))
        self.assertIn("[bridge-warning]", answer)
        self.assertIn("0.162.9", answer)
        self.assertIn("untested_cli_allowed:0.162.9", self.w.telemetry()[-1]["warnings"])

    def test_nonzero_exit_preserves_state_and_never_replays(self):
        svc = self.service()
        svc.execute(CONSULT, "ok")
        before = self.w.registry()
        self.w.set(exec={"exit": 3, "events": [{"type": "thread.started", "thread_id": "{thread}"}],
                         "stderr": "boom: token sk-abcdefghijklmnopqrstuvwxyz0123 failed"})
        exc = run_expect_error(self, svc)
        self.assertEqual((exc.category, exc.code), (errors.SUBPROCESS_FAILED, "nonzero_exit"))
        self.assertTrue(exc.inference_may_have_started)
        self.assertNotIn("sk-abcdefghijkl", str(exc), "secrets in stderr are redacted")
        self.assertEqual(self.w.registry()["sessions"], before["sessions"])
        self.assertEqual(len(self.w.exec_entries()), 2, "exactly one exec per call: no replay")
        rec = self.w.telemetry()[-1]
        self.assertEqual((rec["outcome"], rec["error_category"], rec["exit_code"]), ("error", "subprocess_failed", 3))
        self.assertTrue(rec["inference_may_have_started"])
        self.assertTrue(all(v is None for v in rec["usage"].values()))
        self.assertNotIn("sk-abcdefghijkl", self.w.telemetry_text())

    def test_rate_limit_is_categorised(self):
        self.w.set(exec={"exit": 1, "events": [
            {"type": "thread.started", "thread_id": "{thread}"},
            {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again in 3 hours."}},
        ]})
        exc = run_expect_error(self, self.service())
        self.assertEqual(exc.category, errors.RATE_LIMITED)
        self.assertIn("No retry", str(exc))
        self.assertEqual(len(self.w.exec_entries()), 1)
        self.assertEqual(self.w.telemetry()[0]["error_category"], "rate_limited")

    def test_runtime_auth_failure_is_categorised(self):
        self.w.set(exec={"exit": 1, "stderr": "401 Unauthorized: token expired", "events": []})
        exc = run_expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.AUTHENTICATION, "auth_runtime"))

    def test_incomplete_outputs(self):
        cases = {
            "no_turn_completed": {"events": [{"type": "thread.started", "thread_id": "{thread}"}]},
            "no_thread_id": {"events": [{"type": "turn.completed"}]},
            "no_answer_file": {"answer": None},
            "empty_answer": {"answer": "  \n "},
        }
        for code, spec in cases.items():
            self.setUp()
            self.w.set(exec=spec)
            exc = run_expect_error(self, self.service())
            self.assertEqual((exc.category, exc.code), (errors.OUTPUT_INCOMPLETE, code), code)
            self.assertFalse((self.w.state / "registry.json").exists()
                             and self.w.registry()["sessions"], "state must not be saved")

    def test_thread_mismatch_on_resume_is_not_concealed(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        saved = self.w.registry()["sessions"]
        self.w.set(exec={"thread_id": "99999999-9999-9999-9999-999999999999"})
        exc = run_expect_error(self, svc)
        self.assertEqual((exc.category, exc.code), (errors.SESSION_MISMATCH, "resumed_different_thread"))
        self.assertEqual(self.w.registry()["sessions"], saved)

    def test_missing_saved_session_reported_not_replaced(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        saved = self.w.registry()["sessions"]
        self.w.set(exec={"exit": 1, "stderr": "Error: session 1111 not found", "events": []})
        exc = run_expect_error(self, svc)
        self.assertEqual((exc.category, exc.code), (errors.SESSION_MISMATCH, "session_missing"))
        self.assertEqual(self.w.registry()["sessions"], saved)
        self.assertIn("reset", str(exc))

    def test_corrupt_registry_blocks_inference_and_is_untouched(self):
        self.w.state.mkdir(parents=True)
        (self.w.state / "registry.json").write_bytes(b"{oops")
        exc = run_expect_error(self, self.service())
        self.assertEqual(exc.category, errors.SESSION_CORRUPT)
        self.assertEqual((self.w.state / "registry.json").read_bytes(), b"{oops")
        self.assertEqual(self.w.exec_entries(), [])
        self.assertIn("quarantine", str(exc))

    def test_state_inside_repository_is_refused(self):
        env_state = str(self.w.repo / ".bridge_state")
        exc = run_expect_error(self, self.service(GPT_ARCHITECT_V2_STATE_DIR=env_state))
        self.assertEqual(exc.code, "state_in_repo")
        self.assertFalse((self.w.repo / ".bridge_state").exists(), "nothing may be written into the repo")

    def test_state_pointer_save_failure_does_not_discard_or_replay(self):
        from bridge import state as st

        orig = st.Registry.bind_thread

        def failing(self, *a, **k):
            raise errors.BridgeError(errors.STATE_ERROR, "disk full", code="registry_unwritable")

        st.Registry.bind_thread = failing
        try:
            answer = self.service().execute(CONSULT, "q")
        finally:
            st.Registry.bind_thread = orig
        self.assertTrue(answer.startswith("FAKE ANSWER"))
        self.assertIn("[bridge-warning]", answer)
        self.assertEqual(len(self.w.exec_entries()), 1)
        self.assertIn("state_not_saved:registry_unwritable", self.w.telemetry()[0]["warnings"])


class Telemetry(WorldCase):
    def test_redaction_by_construction(self):
        self.w.set(prompt_marker="PROMPT-SECRET-MARKER", exec={"answer": "ANSWER-SECRET-MARKER"})
        self.service().execute(CONSULT, "please look at PROMPT-SECRET-MARKER in " + str(self.w.repo))
        self.assertTrue(self.w.exec_entries()[0]["prompt_has_marker"])
        text = self.w.telemetry_text()
        for forbidden in ("PROMPT-SECRET-MARKER", "ANSWER-SECRET-MARKER", str(self.w.repo), "GPT ARCHITECT",
                          "sk-", "access_token", "refresh_token", "api_key", "bearer", "authorization",
                          "FAKE ANSWER"):
            self.assertNotIn(forbidden, text, forbidden)
        rec = json.loads(text.splitlines()[0])
        allowed = {"schema_version", "call_id", "tool", "started_at", "ended_at", "route", "session", "queue_ms",
                   "run_ms", "outcome", "error_category", "error_code", "inference_may_have_started", "exit_code",
                   "usage", "events", "prompt_chars", "answer_chars", "cli_version", "warnings",
                   "capabilities", "process", "infra", "repo", "pointer_state", "history", "session_state"}
        self.assertEqual(set(rec), allowed)
        self.assertEqual(set(rec["session"]), {"name", "repo_key", "thread_id", "resumed"})

    def test_exactly_one_terminal_record_per_invocation(self):
        svc = self.service()
        svc.execute(CONSULT, "ok")
        for _ in range(2):
            with self.assertRaises(errors.BridgeError):
                svc.execute(CONSULT, "")
        self.w.set(exec={"exit": 1})
        with self.assertRaises(errors.BridgeError):
            svc.execute(REVIEW, "x")
        recs = self.w.telemetry()
        self.assertEqual(len(recs), 4)
        self.assertEqual(len({r["call_id"] for r in recs}), 4)
        self.assertEqual([r["outcome"] for r in recs], ["success", "error", "error", "error"])

    def test_telemetry_failure_never_fails_or_replays_the_call(self):
        from bridge.telemetry import Telemetry as T

        original = T._rotate_if_needed
        T._rotate_if_needed = lambda self, n: (_ for _ in ()).throw(OSError("disk"))
        try:
            self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        finally:
            T._rotate_if_needed = original
        original_write = T.write

        def raising(self, record):
            raise RuntimeError("telemetry exploded")

        T.write = raising
        try:
            self.assertEqual(self.service().execute(CONSULT, "q2"), "FAKE ANSWER")
        finally:
            T.write = original_write
        self.assertEqual(len(self.w.exec_entries()), 2, "one exec per call; no replay after telemetry failure")
        self.assertEqual(self.w.telemetry(), [])

    def test_rotation_is_bounded(self):
        t = telemetry.Telemetry(self.w.state, max_bytes=600, backups=2)
        for i in range(40):
            t.write({"call_id": f"{i:032d}", "tool": CONSULT, "outcome": "success"})
        names = sorted(p.name for p in (self.w.state / "telemetry").glob("calls*.jsonl"))
        self.assertEqual(names, ["calls.1.jsonl", "calls.2.jsonl", "calls.jsonl"])
        for p in (self.w.state / "telemetry").glob("calls*.jsonl"):
            self.assertLess(p.stat().st_size, 1200)
            for line in p.read_text().splitlines():
                json.loads(line)


class CapabilityAudit(WorldCase):
    scenario = {"mcp_servers": [
        {"name": "idea", "source": "config"},
        {"name": "node_repl", "source": "config"},
        {"name": "cua_repl", "source": "plugin"},
    ]}

    def test_mcp_servers_are_disabled_and_verified_before_inference(self):
        self.service().execute(CONSULT, "q")
        argv = self.w.exec_entries()[0]["argv"]
        joined = " ".join(argv)
        for name in ("idea", "node_repl"):
            self.assertIn(f"mcp_servers.{name}.enabled=false", joined)
        self.assertNotIn("mcp_servers.cua_repl", joined, "plugin servers are removed via --disable plugins")
        for feature in ("hooks", "plugins", "apps", "computer_use"):
            self.assertIn(f"--disable {feature}", joined)
        # the audit listed MCP servers (twice) *before* exec ran
        kinds = [e["kind"] for e in self.w.log_entries()]
        self.assertEqual(kinds.count("mcp_list"), 2)
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "mcp_list"), kinds.index("exec_fresh"))
        self.assertTrue(all("--disable" in e["argv"] for e in self.w.log_entries("mcp_list")))

    def test_audit_failure_blocks_inference(self):
        self.w.set(ignore_overrides=["idea"])
        exc = run_expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "mcp_still_enabled"))
        self.assertIn("idea", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_allow_list_is_explicit_and_reported(self):
        self.service(GPT_ARCHITECT_MCP_ALLOW="idea").execute(CONSULT, "q")
        joined = " ".join(self.w.exec_entries()[0]["argv"])
        self.assertNotIn("mcp_servers.idea.enabled=false", joined)
        self.assertIn("mcp_servers.node_repl.enabled=false", joined)

    def test_hooks_found_are_disabled_and_reported_or_block_when_not_disableable(self):
        (self.w.codex_home / "hooks.json").write_text("{}")
        self.service().execute(CONSULT, "q")
        self.assertIn("--disable hooks", " ".join(self.w.exec_entries()[0]["argv"]))

        self.setUp()
        (self.w.codex_home / "hooks.json").write_text("{}")
        self.w.set(features=["plugins", "apps"])  # CLI without the required gates (hooks, delegation, ...)
        exc = run_expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "required_control_missing"))
        self.assertIn("hooks", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_repo_level_codex_config_with_hooks_is_detected(self):
        (self.w.repo / ".codex").mkdir()
        (self.w.repo / ".codex" / "config.toml").write_text("[hooks]\npre = 'x'\n")
        from bridge import codexcli

        hooks, project = codexcli._find_hook_sources(self.w.repo, self.w.env())
        self.assertIn("repo/.codex/config.toml (hooks table)", hooks)
        self.assertEqual(project, ["repo/.codex/config.toml"])


@unittest.skipUnless(sys.platform == "win32", "sandbox preflight targets the Windows sandbox")
class SandboxPreflight(WorldCase):
    def test_runs_before_inference_with_same_overrides_and_read_only(self):
        self.w.set(mcp_servers=[{"name": "idea", "source": "config"}])
        self.service(GPT_ARCHITECT_WINDOWS_SANDBOX="unelevated").execute(CONSULT, "q")
        kinds = [e["kind"] for e in self.w.log_entries()]
        self.assertEqual(kinds.count("sandbox"), 3)  # process-start + repo-open + source-read stages
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "sandbox"), kinds.index("exec_fresh"))
        first = self.w.log_entries("sandbox")[0]
        joined = " ".join(first["argv"])
        self.assertIn('sandbox_mode="read-only"', joined)
        self.assertIn('windows.sandbox="unelevated"', joined)
        self.assertIn("mcp_servers.idea.enabled=false", joined)
        self.assertIn("--disable hooks", joined)
        self.assertEqual(Path(first["cwd"]).resolve(), self.w.repo.resolve())
        self.assertEqual(first["cmd"], ["cmd", "/c", "cd"])
        self.assertEqual(self.w.log_entries("sandbox")[1]["cmd"], ["git", "rev-parse", "--is-inside-work-tree"])
        self.assertEqual(self.w.log_entries("sandbox")[2]["cmd"][:3], ["cmd", "/c", "type"])
        # the only warning is the explicit, visible backend override (no silent configuration)
        self.assertEqual(self.w.telemetry()[0]["warnings"], ["windows_sandbox_override:unelevated"])

    def test_setup_failure_blocks_inference_with_actionable_error(self):
        from bridge import codexcli

        self.w.set(sandbox="setup_failed")
        original = codexcli.running_lock_holders
        codexcli.running_lock_holders = lambda: [4242, 4343]
        try:
            exc = run_expect_error(self, self.service())
        finally:
            codexcli.running_lock_holders = original
        self.assertEqual((exc.category, exc.code), (errors.SANDBOX_UNAVAILABLE, "sandbox_setup_failed"))
        self.assertFalse(exc.inference_may_have_started)
        text = str(exc)
        self.assertIn("helper_unknown_error", text)
        self.assertIn("4242", text)
        self.assertIn("Quit those apps", text)
        self.assertIn("No model call was made", text)
        self.assertEqual(self.w.exec_entries(), [], "no inference, no allowance")
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["outcome"], rec["error_category"], rec["error_code"]),
                         ("error", "sandbox_unavailable", "sandbox_setup_failed"))
        self.assertFalse(rec["inference_may_have_started"])
        self.assertFalse((self.w.state / "registry.json").exists()
                         and self.w.registry()["sessions"], "no session may be created")

    def test_repo_unreadable_is_distinguished(self):
        self.w.set(sandbox="repo_unreadable")
        exc = run_expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SANDBOX_UNAVAILABLE, "repo_unreadable"))
        self.assertIn("sandbox account", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_failure_does_not_poison_the_next_call_and_lock_is_released(self):
        self.w.set(sandbox="setup_failed")
        svc = self.service()
        run_expect_error(self, svc)
        self.w.set(sandbox="ok")  # e.g. user quit the app
        self.assertEqual(self.service(GPT_ARCHITECT_QUEUE_TIMEOUT="1").execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh"])

    def test_resumed_calls_are_preflighted_too(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        self.w.set(sandbox="setup_failed")
        exc = run_expect_error(self, svc)
        self.assertEqual(exc.category, errors.SANDBOX_UNAVAILABLE)
        self.assertEqual(len(self.w.exec_entries()), 1)
        self.assertEqual(self.w.registry()["sessions"][next(iter(self.w.registry()["sessions"]))]["turns"], 1)

    def test_can_be_disabled_explicitly_and_is_recorded(self):
        self.w.set(sandbox="setup_failed")
        self.assertEqual(self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0").execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(self.w.log_entries("sandbox"), [])
        self.assertIn("sandbox_preflight_skipped:disabled", self.w.telemetry()[0]["warnings"])
        with self.assertRaises(errors.BridgeError):
            self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="maybe").execute(CONSULT, "q")

    def test_status_runs_it_only_on_request(self):
        import contextlib
        import io

        from bridge import admin

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = admin.main(list(argv), self.w.env())
            return rc, out.getvalue()

        rc, out = run("status", "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.w.log_entries("sandbox"), [])
        self.assertNotIn("sandbox_preflight", json.loads(out))
        rc, out = run("status", "--json", "--sandbox")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["sandbox_preflight"]["ok"])
        self.assertEqual(json.loads(out)["sandbox_preflight"]["source_read"], "ok")
        self.w.set(sandbox="setup_failed")
        rc, out = run("status", "--json", "--sandbox")
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["sandbox_preflight"], {"ok": False, "code": "sandbox_setup_failed"})


class TimeoutAndCancellation(WorldCase):
    def test_timeout_kills_process_tree_releases_lock_and_keeps_state(self):
        svc = self.service()
        svc.execute(CONSULT, "seed")
        saved = self.w.registry()["sessions"]
        pidfile = self.w.tmp / "child.pid"
        self.w.set(exec={"sleep": 60, "spawn_child": str(pidfile), "started_file": str(self.w.tmp / "s")})
        t0 = time.monotonic()
        exc = run_expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="2"))
        self.assertLess(time.monotonic() - t0, 25)
        self.assertEqual((exc.category, exc.code), (errors.TIMEOUT, "timeout"))
        self.assertTrue(exc.inference_may_have_started)
        child = int(wait_for(pidfile))
        deadline = time.monotonic() + 10
        while pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(child), "grandchild process must be terminated with the tree")
        self.assertEqual(self.w.registry()["sessions"], saved)
        rec = self.w.telemetry()[-1]
        self.assertEqual((rec["outcome"], rec["error_category"]), ("timeout", "timeout"))
        self.assertEqual(len(self.w.exec_entries()), 2, "no replay after timeout")
        # lock released: an immediate follow-up call is not queued
        self.w.set(exec={})
        self.assertEqual(self.service(GPT_ARCHITECT_QUEUE_TIMEOUT="1").execute(CONSULT, "again"), "FAKE ANSWER")

    def test_cancellation_terminates_children_and_records_terminal_event(self):
        pidfile = self.w.tmp / "child2.pid"
        started = self.w.tmp / "started2"
        self.w.set(exec={"sleep": 60, "spawn_child": str(pidfile), "started_file": str(started)})
        svc = self.service()
        cancel = threading.Event()
        outcome: dict = {}

        def call():
            try:
                svc.execute(CONSULT, "q", cancel)
            except errors.BridgeError as exc:
                outcome["exc"] = exc

        t = threading.Thread(target=call)
        t.start()
        wait_for(started)
        child = int(wait_for(pidfile))
        self.assertTrue(pid_alive(child))
        cancel.set()
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertEqual(outcome["exc"].category, errors.CANCELLED)
        deadline = time.monotonic() + 10
        while pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))
        rec = self.w.telemetry()[-1]
        self.assertEqual(rec["outcome"], "cancelled")
        key = next(iter(state.Registry(self.w.state).snapshot()["sessions"]), None)
        self.assertIsNone(key, "a cancelled first turn must not create a session pointer")

    def test_queue_timeout_when_another_call_holds_the_session(self):
        # Hold the real session lock from this process, then call with a 1s queue limit.
        from bridge import state as st

        ident_root, repo_key = st.canonical_repo(str(self.w.repo))
        key = st.session_key(repo_key, "gpt-6.1-sol", "default")
        reg = st.Registry(self.w.state)
        reg.ensure_dirs()
        lock = reg.session_lock(key)
        lock.acquire(2)
        try:
            exc = run_expect_error(self, self.service(GPT_ARCHITECT_QUEUE_TIMEOUT="1"))
        finally:
            lock.release()
        self.assertEqual(exc.category, errors.QUEUE_TIMEOUT)
        self.assertEqual(self.w.exec_entries(), [])
        rec = self.w.telemetry()[0]
        self.assertGreaterEqual(rec["queue_ms"], 900)
        self.assertIsNone(rec["run_ms"])

    def test_concurrent_calls_on_one_session_are_serialised(self):
        self.w.set(exec={"sleep": 1.0})
        results = []

        def call():
            results.append(self.service().execute(CONSULT, "q"))

        threads = [threading.Thread(target=call) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(results, ["FAKE ANSWER"] * 3)
        kinds = [r["kind"] for r in self.w.exec_entries()]
        self.assertEqual(sorted(kinds), ["exec_fresh", "exec_resume", "exec_resume"])
        self.assertEqual(len(self.w.registry()["sessions"]), 1)
        # one thread only: the two later calls resumed the thread the first created
        tid = next(iter(self.w.registry()["sessions"].values()))["thread_id"]
        for r in self.w.exec_entries():
            if r["kind"] == "exec_resume":
                self.assertEqual(r["argv"][2], tid)


if __name__ == "__main__":
    unittest.main()
