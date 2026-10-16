"""Regression tests for the GPT-review findings F1-F6 (no inference: scripted fake CLI)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, WorldCase, pid_alive, wait_for

from bridge import admin, codexcli, config, errors, fingerprint, jobobject, runner, state

CONSULT, REVIEW = config.CONSULT, config.REVIEW
WIN = sys.platform == "win32"


def expect_error(case: WorldCase, svc, tool=CONSULT, text="q") -> errors.BridgeError:
    with case.assertRaises(errors.BridgeError) as cm:
        svc.execute(tool, text)
    return cm.exception


def thread_started():
    return {"type": "thread.started", "thread_id": "{thread}"}


def completed():
    return {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 4}}


def cmd_item(output, status="failed", exit_code=None, cwd=None, kind="command_execution"):
    item = {"id": "item_1", "type": kind, "command": "git status", "aggregated_output": output,
            "exit_code": exit_code, "status": status}
    if cwd is not None:
        item["cwd"] = cwd
    return {"type": "item.completed", "item": item}


def agent_message(text):
    return {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": text}}


HELPER_ERR = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"


# ------------------------------------------------------------------------- F1
class F1_ActualTurnFailures(WorldCase):
    def test_failed_command_with_helper_unknown_error_then_completed_answer_is_not_a_success(self):
        """The exact regression: failed command item, then turn.completed + plausible answer."""
        self.w.set(exec={"answer": "Architecture looks sound; layering is clean and tests are adequate.",
                         "events": [thread_started(), cmd_item(HELPER_ERR), completed()]})
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SANDBOX_UNAVAILABLE, "turn_sandbox_setup_failed"))
        self.assertTrue(exc.inference_may_have_started)
        self.assertNotIn("layering is clean", str(exc), "the ungrounded answer must be withheld")
        self.assertIn("answer was withheld", str(exc))
        self.assertIn("preflight is only a readiness check", str(exc))
        self.assertEqual(len(self.w.exec_entries()), 1, "no replay")
        # no pointer is bound for an untrustworthy fresh turn; the thread id is preserved for reconciliation
        self.assertFalse((self.w.state / "registry.json").exists() and self.w.registry()["sessions"])
        journal = state.Journal(self.w.state).read()
        self.assertTrue(any(j["event"] == "unbound_thread" and j.get("reason") == "turn_sandbox_setup_failed"
                            for j in journal))
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["outcome"], rec["error_code"]), ("error", "turn_sandbox_setup_failed"))
        self.assertEqual(rec["infra"]["failed_items"], 1)
        self.assertEqual(rec["infra"]["kinds"], {"sandbox_setup": 1, "process_creation": 1})
        self.assertEqual(rec["history"], "turn_may_exist")
        self.assertNotIn("layering", self.w.telemetry_text())

    def test_rollout_style_camelcase_item_is_detected_too(self):
        self.w.set(exec={"events": [thread_started(), cmd_item(HELPER_ERR, kind="CommandExecution"), completed()]})
        self.assertEqual(expect_error(self, self.service()).code, "turn_sandbox_setup_failed")

    def test_ordinary_nonzero_command_exit_is_not_an_infrastructure_failure(self):
        self.w.set(exec={"events": [thread_started(),
                                    cmd_item("diff --git a/x b/x\n+changed", status="failed", exit_code=1),
                                    cmd_item("1 file changed", status="completed", exit_code=0),
                                    completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["infra"], {"command_items": 2, "failed_items": 0, "kinds": {},
                                        "oversize_signature_lines": 0})
        self.assertEqual(rec["outcome"], "success")

    def test_signature_inside_successful_command_output_is_not_a_failure(self):
        # e.g. the model `type`s a source file that merely contains the phrase
        self.w.set(exec={"events": [thread_started(),
                                    cmd_item("raise Error('helper_unknown_error')", status="completed", exit_code=0),
                                    completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_agent_narration_quoting_the_signature_is_not_scanned(self):
        self.w.set(exec={"events": [thread_started(),
                                    agent_message("The code handles helper_unknown_error: setup refresh had errors"),
                                    completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_repo_access_signature_counts_only_when_cwd_is_the_repository(self):
        out = "fatal: not a git repository (or any of the parent directories): .git"
        in_repo = Path(self.w.repo).as_uri()
        self.w.set(exec={"events": [thread_started(), cmd_item(out, exit_code=128, cwd=in_repo), completed()]})
        self.assertEqual(expect_error(self, self.service()).code, "turn_repo_access_failed")

        self.setUp()
        elsewhere = (self.w.tmp / "somewhere_else").as_uri()
        self.w.set(exec={"events": [thread_started(), cmd_item(out, exit_code=128, cwd=elsewhere), completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_failed_resumed_turn_leaves_the_pointer_alone(self):
        svc = self.service()
        svc.execute(CONSULT, "seed")
        before = self.w.registry()["sessions"]
        self.w.set(exec={"events": [thread_started(), cmd_item(HELPER_ERR), completed()]})
        self.assertEqual(expect_error(self, svc).category, errors.SANDBOX_UNAVAILABLE)
        self.assertEqual(self.w.registry()["sessions"], before)

    def test_degraded_mode_was_removed_and_cannot_be_enabled(self):
        """P3: an answer whose actual turn hit infrastructure failures is never returned as a review."""
        self.w.set(exec={"events": [thread_started(), cmd_item(HELPER_ERR), completed()]})
        for value in ("degraded", "ignore", "warn"):
            exc = expect_error(self, self.service(GPT_ARCHITECT_ON_TURN_INFRA_FAILURE=value))
            self.assertEqual((exc.category, exc.code), (errors.INVALID_CONFIGURATION, "bad_env"))
            self.assertIn("removed", str(exc))
        self.assertEqual(self.w.exec_entries(), [], "invalid configuration is rejected before inference")
        # 'error' (the only accepted value) is the default behaviour: still withheld
        exc = expect_error(self, self.service(GPT_ARCHITECT_ON_TURN_INFRA_FAILURE="error"))
        self.assertEqual(exc.code, "turn_sandbox_setup_failed")

    @unittest.skipUnless(WIN, "sandbox preflight is Windows-only")
    def test_preflight_reads_a_source_file_not_just_git_metadata(self):
        self.w.set(sandbox="source_unreadable")
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SANDBOX_UNAVAILABLE, "source_file_unreadable"))
        self.assertIn("does not prove source readability", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_classify_item_unit(self):
        c = runner.classify_item
        self.assertEqual(c({"type": "command_execution", "status": "failed", "aggregated_output": HELPER_ERR},
                           None), (True, ["sandbox_setup", "process_creation"]))
        self.assertEqual(c({"type": "reasoning", "text": HELPER_ERR, "status": "failed"}, None), (False, []))
        self.assertEqual(c({"type": "command_execution", "status": "completed", "exit_code": 0,
                            "aggregated_output": HELPER_ERR}, None), (True, []))
        self.assertEqual(c({"type": "mcp_tool_call", "status": "failed", "error": {"message": "windows sandbox failed"}},
                           None), (True, ["sandbox_setup"]))


# ------------------------------------------------------------------------- F2
class F2_CapabilityContainment(WorldCase):
    def test_delegation_image_generation_and_web_are_controlled_and_reported_separately(self):
        self.service().execute(CONSULT, "q")
        argv = " ".join(self.w.exec_entries()[0]["argv"])
        self.assertIn("--disable multi_agent", argv)
        self.assertIn("--disable image_generation", argv)
        self.assertIn('web_search="disabled"', argv)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            admin.main(["status", "--json"], self.w.env())
        caps = {c["name"]: c for c in json.loads(buf.getvalue())["capabilities"]["capabilities"]}
        self.assertEqual(caps["delegation"]["level"], "resolved")
        self.assertEqual(caps["image_generation"]["level"], "resolved")
        self.assertEqual(caps["web_search"]["level"], "requested")        # no readout exists: not "enforced"
        self.assertEqual(caps["view_image_goals_sleep_skills"]["level"], "uncontained")
        self.assertIn("not been mapped", caps["web_search"]["note"])
        tel = self.w.telemetry()[0]["capabilities"]
        self.assertIn("delegation", tel["resolved"])
        self.assertIn("web_search", tel["requested"])
        self.assertIn("view_image_goals_sleep_skills", tel["uncontained"])

    def test_missing_required_control_fails_closed(self):
        self.w.set(features=[f for f in ("hooks", "plugins", "apps", "browser_use", "in_app_browser",
                                         "computer_use", "image_generation")])  # no multi_agent
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "required_control_missing"))
        self.assertIn("multi_agent", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_gate_that_stays_enabled_after_override_fails_closed(self):
        self.w.set(ignore_feature_disable=["multi_agent"])
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "feature_disable_not_effective"))
        self.assertIn("multi_agent", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_incompatible_features_schema_fails_closed(self):
        self.w.set(features_raw="hooks    stable    true\nTHIS IS NOT THE SCHEMA YOU TESTED")
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "features_schema_incompatible"))
        self.assertEqual(self.w.exec_entries(), [])

    def test_incompatible_mcp_schema_fails_closed(self):
        for raw in ('[{"name": "idea"}]', '[{"name": "idea", "enabled": "yes"}]', '{"servers": []}',
                    '[{"name": "a", "enabled": true}, {"name": "a", "enabled": false}]', "not json"):
            self.setUp()
            self.w.set(mcp_raw=raw)
            exc = expect_error(self, self.service())
            self.assertEqual(exc.category, errors.CAPABILITY_AUDIT, raw)
            self.assertIn(exc.code, ("mcp_schema_incompatible", "mcp_list_unavailable"), raw)
            self.assertEqual(self.w.exec_entries(), [])

    def test_allow_list_default_is_empty_and_exception_is_visible_everywhere(self):
        self.w.set(mcp_servers=[{"name": "docs", "source": "config"}, {"name": "idea", "source": "config"}])
        self.service().execute(CONSULT, "q")
        self.assertIn("mcp_servers.docs.enabled=false", " ".join(self.w.exec_entries()[0]["argv"]))  # default: none allowed

        self.setUp()
        self.w.set(mcp_servers=[{"name": "docs", "source": "config"}, {"name": "idea", "source": "config"}])
        answer = self.service(GPT_ARCHITECT_MCP_ALLOW="docs").execute(CONSULT, "q")
        self.assertIn("[bridge-warning]", answer)
        self.assertIn("GPT_ARCHITECT_MCP_ALLOW", answer)
        self.assertIn("docs", answer)
        self.assertIn("NOT contained by the read-only sandbox", answer)
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["capabilities"]["mcp_allowed"], ["docs"])
        self.assertTrue(any(w.startswith("mcp_allow_list_active") for w in rec["warnings"]))
        for text in (answer, self.w.telemetry_text()):
            self.assertNotIn(str(self.w.repo), text)
            self.assertNotIn(str(self.w.home), text)

    def test_sandbox_backend_override_is_visible_not_silent(self):
        answer = self.service(GPT_ARCHITECT_WINDOWS_SANDBOX="unelevated").execute(CONSULT, "q")
        self.assertIn("backend is overridden to 'unelevated'", answer)
        self.assertIn("no apply_patch/shell attack-test evidence", answer)
        self.assertIn("windows_sandbox_override:unelevated", self.w.telemetry()[0]["warnings"])
        self.setUp()
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")  # default: no banner

    def test_exact_version_baseline_not_a_series(self):
        self.assertEqual(codexcli.SUPPORTED_CLI_VERSIONS, ("0.162.1",))
        self.assertIsNone(codexcli.check_compat("0.162.1", False))
        for v in ("0.162.0", "0.162.2", "0.163.0", "1.0.0"):
            with self.assertRaises(errors.BridgeError, msg=v):
                codexcli.check_compat(v, False)
        self.assertEqual(codexcli.check_compat("0.162.2", True), "untested_cli_allowed:0.162.2")

    def test_parse_features_is_strict(self):
        ok = codexcli.parse_features("hooks  stable  true\nfoo_bar  under development  false\n")
        self.assertEqual(ok, {"hooks": True, "foo_bar": False})
        for bad in ("hooks stable maybe", "hooks", "hooks stable true extra", "HOOKS stable true"):
            with self.assertRaises(errors.BridgeError, msg=bad):
                codexcli.parse_features(bad)


# ------------------------------------------------------------------------- F3
@unittest.skipUnless(WIN, "Job Objects are Windows-only (POSIX uses process groups)")
class F3_ProcessOwnership(WorldCase):
    def test_timeout_is_confirmed_through_the_job_not_assumed(self):
        pidfile = self.w.tmp / "child.pid"
        self.w.set(exec={"sleep": 60, "spawn_child": str(pidfile)})
        exc = expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="2"))
        self.assertEqual(exc.category, errors.TIMEOUT)
        self.assertIn("termination CONFIRMED", str(exc))
        self.assertIn("Codex history MAY contain", str(exc))
        self.assertNotIn("unchanged", str(exc).replace("Bridge pointer: unchanged", ""))
        child = int(wait_for(pidfile))
        deadline = time.monotonic() + 10
        while pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))
        proc = self.w.telemetry()[0]["process"]
        self.assertEqual((proc["ownership"], proc["termination"], proc["active_after"]), ("job_object", "confirmed", 0))
        self.assertTrue(proc["service_children_unobservable"])
        self.assertIn("Windows sandbox service", str(exc))
        marker = state.Registry(self.w.state).read_marker("inflight", self._key())
        self.assertEqual((marker["state"], marker["reason"]), ("uncertain", "service_children_unobservable"))
        self.assertEqual(self.w.telemetry()[0]["session_state"], "in_doubt")
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        self.assertEqual(len(self.w.exec_entries()), 1)

    def _key(self):
        root, repo_key = state.canonical_repo(str(self.w.repo))
        return state.session_key(repo_key, "gpt-6.1-sol", "default")

    def test_descendant_surviving_parent_exit_is_reaped_not_ignored(self):
        pidfile = self.w.tmp / "lingering.pid"
        self.w.set(exec={"spawn_child": str(pidfile)})  # fake exits 0 immediately, child keeps running
        answer = self.service().execute(CONSULT, "q")
        self.assertIn("FAKE ANSWER", answer)
        child = int(wait_for(pidfile))
        deadline = time.monotonic() + 10
        while pid_alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(child), "a descendant must not outlive the call")
        rec = self.w.telemetry()[0]
        # the count is of live job *members* (a venv python.exe is a launcher + interpreter pair)
        self.assertGreaterEqual(rec["process"]["reaped_descendants"], 1)
        self.assertEqual(rec["process"]["termination"], "confirmed")
        self.assertEqual(rec["process"]["active_after"], 0)
        self.assertTrue(any(w.startswith("reaped_descendants:") for w in rec["warnings"]))

    def test_unconfirmed_termination_blocks_session_reuse_until_reconciled(self):
        pidfile = self.w.tmp / "child2.pid"
        self.w.set(exec={"sleep": 60, "spawn_child": str(pidfile)})
        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", return_value=1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda self: None), \
                mock.patch.object(codexcli, "kill_tree", lambda proc, grace_s=5.0: (proc.kill(), False)[1]):
            exc = expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="2"))
        self.assertEqual(exc.category, errors.TIMEOUT)
        self.assertIn("NOT confirmed", str(exc).replace("NOT CONFIRMED", "NOT confirmed"))
        self.assertNotIn("termination CONFIRMED", str(exc))
        self.assertIn("blocked until `bridge_admin.py reconcile --yes`", str(exc))
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["process"]["termination"], "unconfirmed")
        self.assertEqual(rec["session_state"], "in_doubt")

        blocked = expect_error(self, self.service())
        self.assertEqual((blocked.category, blocked.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"))
        self.assertEqual(len(self.w.exec_entries()), 1, "a second turn must not run against a possibly live one")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(admin.main(["reconcile", "--yes"], self.w.env()), 0)
        self.assertIn("No process was touched", out.getvalue())
        self.w.set(exec={})
        self.assertEqual(self.service().execute(CONSULT, "again"), "FAKE ANSWER")
        events = [j["event"] for j in state.Journal(self.w.state).read()]
        self.assertIn("termination_unconfirmed", events)
        self.assertIn("operator_reconciled", events)
        # cleanup of the real fake child (the mocks prevented the bridge from doing it)
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/PID", pidfile.read_text().strip(), "/F"], capture_output=True)

    def test_unconfirmed_after_a_successful_turn_returns_answer_but_blocks_the_session(self):
        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", return_value=2), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda self: None):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("FAKE ANSWER", answer)
        self.assertIn("NOT confirmed", answer)
        self.assertIn("reconcile", answer)
        self.assertEqual(self.w.telemetry()[0]["outcome"], "success_session_in_doubt")
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")

    def test_bridge_hard_termination_takes_the_owned_children_with_it(self):
        pidfile = self.w.tmp / "owned_child.pid"
        helper = ROOT / "tests" / "hard_kill_helper.py"
        proc = subprocess.Popen([sys.executable, str(helper), str(pidfile)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            child = int(wait_for(pidfile, 30))
            self.assertTrue(pid_alive(child))
            proc.kill()  # TerminateProcess: no cleanup handlers run in the "bridge"
            proc.wait(10)
            deadline = time.monotonic() + 15
            while pid_alive(child) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(pid_alive(child), "KILL_ON_JOB_CLOSE must reap the owned tree")
        finally:
            with contextlib.suppress(Exception):
                proc.kill()

    def test_stale_marker_after_hard_termination_is_reconciled_only_when_job_owned(self):
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        key = self._key()
        marker = {"state": "running", "call_id": "dead", "owner_pid": 1, "thread_id": None,
                  "ownership": "job_object"}
        reg.write_marker("inflight", key, marker)
        answer = self.service().execute(CONSULT, "q")
        self.assertIn("hard-terminated", answer)
        self.assertIn("commands started outside that job", answer)
        self.assertIn("previous_call_hard_terminated_job_closed", self.w.telemetry()[0]["warnings"])
        self.assertIn("auto_reconciled_hard_termination", [j["event"] for j in reg.journal.read()])

        self.setUp()
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        reg.write_marker("inflight", self._key(), {**marker, "ownership": "none"})
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "stale_inflight_unowned"))

    def test_nothing_outside_the_owned_tree_is_ever_killed(self):
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            self.w.set(exec={"sleep": 60})
            expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="2"))
            time.sleep(0.5)
            self.assertIsNone(bystander.poll(), "an unrelated process must survive the bridge's cleanup")
        finally:
            bystander.kill()

    def test_ownership_failure_refuses_the_turn_before_anything_executes(self):
        """N3: no ownership => no inference.  Previously a normal exit would have 'succeeded'."""
        for sleep in (60, 0):   # long-running child AND one that would exit normally
            self.setUp()
            started = self.w.tmp / "started"
            self.w.set(exec={"sleep": sleep, "started_file": str(started)})
            with mock.patch.object(jobobject.ProcessOwnership, "attach", lambda self, proc: False):
                exc = expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="30"))
            self.assertEqual((exc.category, exc.code), (errors.SUBPROCESS_FAILED, "ownership_unavailable"))
            self.assertFalse(exc.inference_may_have_started)
            self.assertIn("before it executed and before any prompt was written", str(exc))
            self.assertFalse(started.exists(), "the suspended child must never have run")
            self.assertEqual(self.w.exec_entries(), [], "the fake CLI's exec path never ran")
            rec = self.w.telemetry()[0]
            self.assertEqual((rec["outcome"], rec["error_code"]), ("error", "ownership_unavailable"))
            self.assertFalse(rec["inference_may_have_started"])
            self.assertEqual(rec["history"], "no_turn_started")
            self.assertIsNone(state.Registry(self.w.state).read_marker("inflight", self._key()),
                              "nothing ran, so nothing to reconcile")
            # outside the patch, ownership works again and the very same session proceeds normally
            self.w.set(exec={})
            self.assertEqual(self.service().execute(CONSULT, "works once ownership is available"), "FAKE ANSWER")


# ------------------------------------------------------------------------- F4
class F4_RegistryLifecycle(WorldCase):
    def test_first_initialisation_is_allowed_and_marks_the_instance_established(self):
        self.assertFalse((self.w.state / "instance.json").exists())
        self.service().execute(CONSULT, "q")
        inst = json.loads((self.w.state / "instance.json").read_text())
        self.assertTrue(inst["registry_established"])
        self.assertEqual(self.w.registry()["instance_id"], inst["instance_id"])

    def test_loss_of_an_established_registry_never_starts_replacement_sessions(self):
        self.service().execute(CONSULT, "q")
        (self.w.state / "registry.json").unlink()
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_CORRUPT, "registry_lost"))
        self.assertIn("will not silently start replacement", str(exc))
        self.assertEqual(len(self.w.exec_entries()), 1, "no new thread may be created")
        self.assertFalse((self.w.state / "registry.json").exists(), "the lost registry must not be recreated")

    def test_restore_prev_recovers_the_pointer(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        svc.execute(CONSULT, "b")
        thread = next(iter(self.w.registry()["sessions"].values()))["thread_id"]
        (self.w.state / "registry.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(admin.main(["restore-prev", "--yes"], self.w.env()), 0)
        svc.execute(CONSULT, "c")
        self.assertEqual(self.w.exec_entries()[-1]["argv"][2], thread)  # resumed the same thread
        self.assertFalse((self.w.state / "maintenance.json").exists())

    def test_quarantine_requires_quiescence_and_leaves_state_intact_when_busy(self):
        self.service().execute(CONSULT, "q")
        before = (self.w.state / "registry.json").read_bytes()
        root, repo_key = state.canonical_repo(str(self.w.repo))
        reg = state.Registry(self.w.state)
        lock = reg.session_lock(state.session_key(repo_key, "gpt-6.1-sol", "default"))
        lock.acquire(2)  # an in-progress call
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = admin.main(["quarantine", "--yes"], self.w.env())
        finally:
            lock.release()
        self.assertEqual(rc, 1)
        self.assertIn("not_quiescent", err.getvalue())
        self.assertEqual((self.w.state / "registry.json").read_bytes(), before)
        self.assertFalse((self.w.state / "maintenance.json").exists(), "the flag must not stay behind")

    def test_after_quarantine_calls_refuse_until_explicit_reinit_and_cannot_repopulate(self):
        self.service().execute(CONSULT, "q")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(admin.main(["quarantine", "--yes"], self.w.env()), 0)
        self.assertEqual(len(list(self.w.state.glob("registry.corrupt-*.json"))), 1)
        self.assertEqual(expect_error(self, self.service()).code, "registry_lost")
        # a call that was in flight during quarantine finishes and tries to save its pointer:
        root, repo_key = state.canonical_repo(str(self.w.repo))
        reg = state.Registry(self.w.state)
        ident = {"repo_key": repo_key, "repo_path": str(root), "model": "gpt-6.1-sol", "session": "default"}
        with self.assertRaises(errors.BridgeError) as cm:
            reg.bind_thread(state.session_key(repo_key, "gpt-6.1-sol", "default"), ident,
                            "aaaaaaaa-1111-2222-3333-444444444444", None)
        self.assertEqual(cm.exception.code, "registry_lost")
        self.assertFalse((self.w.state / "registry.json").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(admin.main(["reinit", "--yes"], self.w.env()), 0)
        self.service().execute(CONSULT, "after reinit")  # explicit, new empty registry
        self.assertEqual([r["kind"] for r in self.w.exec_entries()], ["exec_fresh", "exec_fresh"])

    def test_maintenance_flag_blocks_new_calls_until_cleared(self):
        self.service().execute(CONSULT, "q")
        reg = state.Registry(self.w.state)
        reg.begin_maintenance("test")
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.STATE_ERROR, "state_maintenance"))
        reg.end_maintenance()
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_registry_from_another_instance_is_refused(self):
        self.service().execute(CONSULT, "q")
        data = json.loads((self.w.state / "registry.json").read_text())
        data["instance_id"] = "f" * 32
        (self.w.state / "registry.json").write_text(json.dumps(data))
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_MISMATCH, "registry_instance_mismatch"))

    def test_reinit_refuses_when_a_registry_exists(self):
        self.service().execute(CONSULT, "q")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(admin.main(["reinit", "--yes"], self.w.env()), 1)
        self.assertIn("registry_exists", err.getvalue())

    def test_corrupt_registry_with_established_instance_stays_untouched(self):
        self.service().execute(CONSULT, "q")
        (self.w.state / "registry.json").write_bytes(b"{broken")
        exc = expect_error(self, self.service())
        self.assertEqual(exc.category, errors.SESSION_CORRUPT)
        self.assertEqual((self.w.state / "registry.json").read_bytes(), b"{broken")


# ------------------------------------------------------------------------- F5
class F5_PartialOutcomes(WorldCase):
    def _fail_binding(self):
        def failing(self, *a, **k):
            raise errors.BridgeError(errors.STATE_ERROR, "disk full", code="registry_unwritable")

        return mock.patch.object(state.Registry, "bind_thread", failing)

    def test_pointer_save_failure_is_partial_success_blocks_silent_threads_and_is_recoverable(self):
        with self._fail_binding():
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("FAKE ANSWER", answer)
        self.assertIn("PARTIAL SUCCESS", answer)
        self.assertIn("exists in Codex history", answer)
        self.assertIn("bridge_admin.py adopt --thread", answer)
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["outcome"], rec["pointer_state"], rec["session_state"]),
                         ("success_partial_persistence", "pending_reconcile", "pending_reconcile"))
        thread = rec["session"]["thread_id"]
        self.assertTrue(thread)
        self.assertIn(thread, answer)

        # the bridge must not silently create another thread
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "pending_persistence"))
        self.assertIn(thread, str(exc))
        self.assertEqual(len(self.w.exec_entries()), 1)

        # explicit adoption recovers the same thread
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(admin.main(["adopt", "--thread", thread, "--model", "gpt-6.1-sol", "--yes"],
                                        self.w.env()), 0)
        self.service().execute(CONSULT, "again")
        self.assertEqual(self.w.exec_entries()[-1]["argv"][2], thread)

    def test_without_a_pending_marker_the_retained_inflight_marker_still_blocks_reuse(self):
        real = state.Registry.write_marker

        def no_pending(self, kind, key, payload):
            return False if kind == "pending" else real(self, kind, key, payload)

        with self._fail_binding(), mock.patch.object(state.Registry, "write_marker", no_pending):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("the pending marker could not be written either", answer)
        self.assertIn("retained in-flight marker", answer)
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["pointer_state"], rec["outcome"]), ("bind_failed", "success_partial_persistence"))
        blocked = expect_error(self, self.service())          # N6: no silent replacement thread
        self.assertEqual((blocked.category, blocked.code), (errors.SESSION_IN_DOUBT, "pending_persistence"))
        self.assertEqual(len(self.w.exec_entries()), 1)

    def test_reset_discards_a_pending_marker_without_touching_history(self):
        with self._fail_binding():
            self.service().execute(CONSULT, "q")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(admin.main(["reset", "--yes", "--model", "gpt-6.1-sol"], self.w.env()), 0)
        self.assertIn("discarded pending marker", out.getvalue())
        self.assertIn("Codex history was not touched", out.getvalue())
        self.service().execute(CONSULT, "fresh start is now explicit")

    def test_resumed_turn_with_counter_save_failure_is_not_a_pending_state(self):
        svc = self.service()
        svc.execute(CONSULT, "seed")
        with self._fail_binding():
            answer = svc.execute(CONSULT, "again")
        self.assertIn("already references this thread", answer)
        self.assertEqual(self.w.telemetry()[-1]["pointer_state"], "unchanged")
        self.assertIn("FAKE ANSWER", svc.execute(CONSULT, "third"))

    def test_interrupted_fresh_turn_preserves_thread_id_and_does_not_claim_history_is_unchanged(self):
        self.w.set(exec={"sleep": 60})
        exc = expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="2"))
        text = str(exc)
        self.assertIn("Bridge pointer: unchanged", text)
        self.assertIn("Codex history MAY contain a partial or complete turn", text)
        self.assertNotIn("Codex history is unaffected", text)
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["history"], rec["pointer_state"]), ("turn_may_exist", "unchanged"))
        self.assertTrue(rec["inference_may_have_started"])

    def test_no_turn_started_is_the_only_case_that_says_history_is_unaffected(self):
        self.w.set(auth="none")
        exc = expect_error(self, self.service())
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["history"], "no_turn_started")
        self.assertFalse(rec["inference_may_have_started"])

    def test_journal_and_telemetry_never_contain_prompt_source_or_answer(self):
        self.w.set(prompt_marker="PROMPT-SECRET", exec={"answer": "ANSWER-SECRET"})
        with self._fail_binding():
            self.service().execute(CONSULT, "contains PROMPT-SECRET in " + str(self.w.repo))
        blob = (self.w.state / "journal.jsonl").read_text() + self.w.telemetry_text() + json.dumps(
            state.Registry(self.w.state).list_markers("pending"))
        for forbidden in ("PROMPT-SECRET", "ANSWER-SECRET", str(self.w.repo)):
            self.assertNotIn(forbidden, blob)


# ------------------------------------------------------------------------- F6
class F6_ReviewConsistency(WorldCase):
    def test_change_during_the_review_is_detected_and_reported_without_content(self):
        self.w.set(exec={"mutate": {"path": "SECRET_WORKING_FILE.txt", "content": "TOP-SECRET-SOURCE-BODY"}})
        answer = self.service().execute(REVIEW, "focus")
        self.assertIn("FAKE ANSWER", answer)
        self.assertIn("The repository CHANGED while GPT was reviewing it", answer)
        self.assertIn("Session locks do not freeze the repository", answer)
        rec = self.w.telemetry()[0]
        self.assertTrue(rec["repo"]["comparable"])
        self.assertTrue(rec["repo"]["changed"])
        self.assertFalse(rec["repo"]["head_changed"])
        self.assertIn("repo_changed_during_call", rec["warnings"])
        self.assertEqual(len(rec["repo"]["before"]), 16)
        blob = self.w.telemetry_text() + answer
        self.assertNotIn("TOP-SECRET-SOURCE-BODY", blob)
        self.assertNotIn("SECRET_WORKING_FILE", self.w.telemetry_text())

    def test_unchanged_repository_has_no_warning_and_is_comparable(self):
        answer = self.service().execute(REVIEW, "focus")
        self.assertEqual(answer, "FAKE ANSWER")
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["repo"]["comparable"], rec["repo"]["changed"]), (True, False))
        self.assertEqual(rec["repo"]["before"], rec["repo"]["after"])

    def test_fingerprint_sees_staged_unstaged_untracked_and_head_changes(self):
        env = dict(__import__("os").environ)
        base = fingerprint.repo_fingerprint(self.w.repo, env)
        self.assertTrue(base["ok"])
        (self.w.repo / "README.md").write_text("# edited\n")
        unstaged = fingerprint.repo_fingerprint(self.w.repo, env)
        self.w.git("add", "README.md")
        staged = fingerprint.repo_fingerprint(self.w.repo, env)
        (self.w.repo / "new_untracked.txt").write_text("x")
        untracked = fingerprint.repo_fingerprint(self.w.repo, env)
        (self.w.repo / "new_untracked.txt").write_text("xyz")
        untracked_edit = fingerprint.repo_fingerprint(self.w.repo, env)
        self.w.git("-c", "user.email=t@e.i", "-c", "user.name=t", "commit", "-q", "-m", "c2")
        committed = fingerprint.repo_fingerprint(self.w.repo, env)
        digests = [base, unstaged, staged, untracked, untracked_edit, committed]
        self.assertEqual(len({d["digest"] for d in digests}), len(digests), "every state must differ")
        self.assertNotEqual(base["head"], committed["head"])

    def test_fingerprint_does_not_write_into_git_dir(self):
        before = {p.name: p.stat().st_mtime_ns for p in (self.w.repo / ".git").iterdir() if p.is_file()}
        fingerprint.repo_fingerprint(self.w.repo, dict(__import__("os").environ))
        after = {p.name: p.stat().st_mtime_ns for p in (self.w.repo / ".git").iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_unavailable_fingerprint_is_reported_not_hidden(self):
        with mock.patch.object(fingerprint, "repo_fingerprint",
                               return_value={"ok": False, "head": None, "digest": None, "reason": "timeout"}):
            self.service().execute(CONSULT, "q")
        self.assertIn("repo_fingerprint_unavailable:timeout", self.w.telemetry()[0]["warnings"])


if __name__ == "__main__":
    unittest.main()
