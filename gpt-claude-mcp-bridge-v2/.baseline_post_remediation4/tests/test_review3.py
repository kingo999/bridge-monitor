"""Regression tests for the third remediation (GPT verification findings V1-V15).

Real child processes (the scripted fake CLI, real Job Objects, real git) and no model.  Every test
names the finding it pins and was written to FAIL against the source GPT reviewed
(`.baseline_post_remediation2`).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, WorldCase, pid_alive

from bridge import (admin, codexcli, config, errors, execpath, fingerprint, gitsafe, ids, jobobject, runner,
                    service, state, telemetry)

CONSULT = config.CONSULT
WIN = sys.platform == "win32"
THREAD = "aaaaaaaa-1111-2222-3333-444444444444"
HELPER = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"


def expect_error(case, svc, tool=CONSULT, text="q") -> errors.BridgeError:
    with case.assertRaises(errors.BridgeError) as cm:
        svc.execute(tool, text)
    return cm.exception


def run_admin(env, *argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = admin.main(list(argv), env)
    return rc, out.getvalue(), err.getvalue()


class Base(WorldCase):
    def key(self) -> str:
        root, repo_key = state.canonical_repo(str(self.w.repo))
        return state.session_key(repo_key, "gpt-6.1-sol", "default")

    def registry(self) -> state.Registry:
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        return reg

    def stale_marker(self, **overrides) -> state.Registry:
        """A dead bridge's in-flight record."""
        reg = self.registry()
        marker = {"state": "running", "phase": "running", "call_id": "c" * 8, "owner_pid": 2_000_000_000,
                  "owner_identity": "1", "thread_id": None, "ownership": "job_object", "resumed": False,
                  "child_pid": 1234, "model": "gpt-6.1-sol"}
        marker.update(overrides)
        self.assertTrue(reg.write_marker("inflight", self.key(), marker))
        return reg


# =========================================================================== V1
@unittest.skipUnless(WIN, "Job Objects / suspended start are Windows-only")
class V1_RunnerCleanup(Base):
    SPAWN_THEN_EXIT = ("import subprocess, sys\n"
                       "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],\n"
                       "                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                       "                     stderr=subprocess.DEVNULL)\n"
                       "open(sys.argv[1], 'w').write(str(c.pid))\n")

    def _run(self, argv, cancel=None, **kw):
        return runner.run_turn(argv, "prompt", dict(os.environ), str(self.w.repo), 20, cancel or threading.Event(),
                               **kw)

    def _fail_start_of(self, thread_name: str, delay: float = 0.0):
        real = threading.Thread.start

        def start(thread):
            if thread.name == thread_name:
                time.sleep(delay)
                raise RuntimeError("can't start new thread")
            return real(thread)

        return mock.patch.object(threading.Thread, "start", start)

    def test_a_failed_thread_start_is_a_categorised_failure_and_the_job_is_released(self):
        closed = []
        real_close = jobobject.ProcessOwnership.close

        def spy(owner):
            closed.append(True)
            return real_close(owner)

        popen_seen = []
        real_popen = subprocess.Popen

        def capture(argv, *a, **k):
            proc = real_popen(argv, *a, **k)
            popen_seen.append(proc)
            return proc

        # the fake CLI blocks reading the prompt that is never written: a live child to clean up
        argv = [sys.executable, str(ROOT / "tests" / "fake_codex.py"), "exec"]
        env = dict(os.environ, FAKE_CODEX_SCENARIO=str(self.w.scenario_path))
        with self._fail_start_of("codex-write_stdin"), mock.patch.object(jobobject.ProcessOwnership, "close", spy), \
                mock.patch.object(runner.subprocess, "Popen", capture):
            with self.assertRaises(errors.BridgeError) as cm:
                runner.run_turn(argv, "prompt", env, str(self.w.repo), 20, threading.Event())
        self.assertEqual((cm.exception.category, cm.exception.code), (errors.SUBPROCESS_FAILED, "runner_failed"))
        self.assertFalse(cm.exception.inference_may_have_started, "the prompt writer never started")
        self.assertTrue(closed, "the job handle must be released (ProcessOwnership.close was never called)")
        self.assertTrue(popen_seen and all(p.poll() is not None for p in popen_seen), "the child must be dead")

    def test_a_reader_thread_failing_to_start_never_lets_the_prompt_be_written(self):
        argv = [sys.executable, str(ROOT / "tests" / "fake_codex.py"), "exec"]
        env = dict(os.environ, FAKE_CODEX_SCENARIO=str(self.w.scenario_path))
        with self._fail_start_of("codex-read_stderr"):
            with self.assertRaises(errors.BridgeError) as cm:
                runner.run_turn(argv, "SECRET PROMPT", env, str(self.w.repo), 20, threading.Event())
        self.assertEqual(cm.exception.code, "runner_failed")
        self.assertFalse(cm.exception.inference_may_have_started)
        self.assertEqual(self.w.exec_entries(), [], "no prompt was delivered, so the fake never logged an exec")

    def test_constructors_after_popen_are_inside_the_cleanup_scope(self):
        popen_seen = []
        real_popen = subprocess.Popen

        def capture(argv, *a, **k):
            proc = real_popen(argv, *a, **k)
            popen_seen.append(proc)
            return proc

        argv = [sys.executable, str(ROOT / "tests" / "fake_codex.py"), "exec"]
        env = dict(os.environ, FAKE_CODEX_SCENARIO=str(self.w.scenario_path))
        for target in ("ProcessOwnership", "Events"):
            popen_seen.clear()
            with mock.patch.object(runner.subprocess, "Popen", capture), \
                    mock.patch.object(runner, target, side_effect=MemoryError("boom")):
                with self.assertRaises(errors.BridgeError) as cm:
                    runner.run_turn(argv, "p", env, str(self.w.repo), 20, threading.Event())
            self.assertEqual(cm.exception.code, "runner_failed", target)
            self.assertTrue(popen_seen and all(p.poll() is not None for p in popen_seen),
                            f"the suspended child must not outlive a failing {target}() constructor")

    def test_a_surviving_descendant_is_killed_when_setup_fails_after_the_root_exited(self):
        pidfile = self.w.tmp / "grandchild.pid"
        argv = [sys.executable, "-c", self.SPAWN_THEN_EXIT, str(pidfile)]
        # let the root spawn its descendant and exit BEFORE the writer thread fails to start
        with self._fail_start_of("codex-write_stdin", delay=1.5):
            with self.assertRaises(errors.BridgeError) as cm:
                runner.run_turn(argv, "p", dict(os.environ), str(self.w.repo), 20, threading.Event())
        self.assertEqual(cm.exception.code, "runner_failed")
        grandchild = int(pidfile.read_text().strip())
        deadline = time.monotonic() + 10
        while pid_alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(grandchild), "a descendant of an already-exited root must be terminated")


# =========================================================================== V2 / V3 / V8
class V2V3_StaleRecoveryNeverDeletesTheOnlyRecord(Base):
    def test_completed_unbound_survives_a_failed_pending_write_and_keeps_blocking(self):
        reg = self.stale_marker(phase="completed_unbound", thread_id=THREAD)
        real = state.Registry.write_marker

        def no_pending(self_, kind, key, payload):
            return False if kind == "pending" else real(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "write_marker", no_pending):
            for _ in range(2):    # the second call must be blocked too, not "recovered"
                exc = expect_error(self, self.service())
                self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"))
            # R2: the thread debt lives in the primary record while the pending marker is unwritable
            self.assertEqual(reg.read_marker("inflight", self.key())["unbound_thread"], THREAD)
            rc, _, _ = run_admin(self.w.env(), "reconcile", "--yes")
            self.assertEqual(rc, 1, "reconcile must not discard a debt it could not persist")
        self.assertIsNotNone(reg.read_marker("inflight", self.key()), "the only durable record was deleted")
        self.assertEqual(self.w.exec_entries(), [], "no replacement thread may start")
        # once the pending marker CAN be written, reconcile converts the debt, then clears process uncertainty
        rc, _, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(reg.read_marker("pending", self.key())["thread_id"], THREAD)
        self.assertEqual(expect_error(self, self.service()).code, "pending_persistence")

    def test_a_dead_owner_with_a_running_phase_is_never_auto_recovered(self):
        """V2.2: the bridge died before the completed-phase write; the turn may have completed."""
        reg = self.stale_marker(thread_id=THREAD)          # fresh call, thread observed, phase=running
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"))
        self.assertEqual(self.w.exec_entries(), [])
        inflight = reg.read_marker("inflight", self.key())
        self.assertEqual((inflight["state"], inflight["reason"]), ("uncertain", "bridge_terminated"))
        # a fresh call that had observed its thread leaves a SEPARATE disposition debt
        self.assertEqual(reg.read_marker("pending", self.key())["thread_id"], THREAD)
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        self.assertEqual(self.w.exec_entries(), [])

    def test_hard_owner_death_requires_operator_reconciliation_even_when_job_owned(self):
        """V3: the OS closed the job, but commands started by the sandbox SERVICE are outside it."""
        for ownership in ("job_object", "none"):
            self.setUp()
            reg = self.stale_marker(ownership=ownership, resumed=True, thread_id=THREAD)
            exc = expect_error(self, self.service())
            self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"), ownership)
            self.assertIn("sandbox service", str(exc))
            self.assertEqual(self.w.exec_entries(), [])
            self.assertIsNone(reg.read_marker("pending", self.key()), "a resumed call has no unbound thread")

    def test_the_recovery_workflow_separates_process_confirmation_from_thread_disposition(self):
        reg = self.stale_marker(thread_id=THREAD)
        expect_error(self, self.service())
        env = self.w.env()
        rc, out, _ = run_admin(env, "reconcile", "--yes")
        self.assertEqual(rc, 0)
        self.assertIn(f"Thread {THREAD} still needs a disposition", out)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(expect_error(self, self.service()).code, "pending_persistence",
                         "reconcile confirms processes only: the thread still blocks a silent replacement")
        rc, out, _ = run_admin(env, "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 0)
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(self.w.exec_entries()[0]["kind"], "exec_resume")


class V8_RetainedMarkerRecovery(Base):
    def _persistence_failed(self):
        """Pointer unsaved AND pending marker unwritable: the in-flight record is the only trace."""
        real = state.Registry.write_marker

        def no_pending(self_, kind, key, payload):
            return False if kind == "pending" else real(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "bind_thread",
                               mock.Mock(side_effect=errors.BridgeError(errors.STATE_ERROR, "x",
                                                                        code="registry_unwritable"))), \
                mock.patch.object(state.Registry, "write_marker", no_pending):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("PARTIAL SUCCESS", answer)
        reg = state.Registry(self.w.state)
        marker = reg.read_marker("inflight", self.key())
        self.assertEqual(marker["state"], "persistence_failed")
        return reg, marker["unbound_thread"]

    def test_reconcile_does_not_discard_an_unsaved_thread(self):
        reg, thread = self._persistence_failed()
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 1)
        self.assertIn("adopt", out)
        self.assertIsNotNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(expect_error(self, self.service()).code, "pending_persistence")

    def test_adopt_settles_the_retained_marker_and_the_session_continues_on_that_thread(self):
        reg, thread = self._persistence_failed()
        rc, out, _ = run_admin(self.w.env(), "adopt", "--thread", thread, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 0)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(self.service().execute(CONSULT, "again"), "FAKE ANSWER")

    def test_adopting_a_different_thread_does_not_settle_the_record_of_another(self):
        reg, thread = self._persistence_failed()
        run_admin(self.w.env(), "adopt", "--thread", "bbbbbbbb-1111-2222-3333-444444444444",
                  "--model", "gpt-6.1-sol", "--yes")
        self.assertIsNotNone(reg.read_marker("inflight", self.key()))

    def test_reset_discards_the_disposition_debt(self):
        reg, thread = self._persistence_failed()
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0)
        self.assertIsNone(reg.read_marker("inflight", self.key()))

    def test_independent_process_uncertainty_survives_adopt_and_reset(self):
        """in doubt AND the pointer/pending writes failed: two facts, two resolutions."""
        real = state.Registry.write_marker

        def no_pending(self_, kind, key, payload):
            return False if kind == "pending" else real(self_, kind, key, payload)

        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", lambda owner: 1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda owner: None), \
                mock.patch.object(runner, "EXIT_ACCOUNTING_GRACE_S", 0.05), \
                mock.patch.object(state.Registry, "bind_thread",
                                  mock.Mock(side_effect=errors.BridgeError(errors.STATE_ERROR, "x",
                                                                           code="registry_unwritable"))), \
                mock.patch.object(state.Registry, "write_marker", no_pending):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("PARTIAL SUCCESS", answer)
        reg = state.Registry(self.w.state)
        marker = reg.read_marker("inflight", self.key())
        self.assertEqual(marker["state"], "uncertain", "process uncertainty must not be overwritten")
        thread = marker["unbound_thread"]
        self.assertTrue(thread)
        rc, out, _ = run_admin(self.w.env(), "adopt", "--thread", thread, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 0)
        after = reg.read_marker("inflight", self.key())
        self.assertEqual(after["state"], "uncertain")
        self.assertIsNone(after.get("unbound_thread"))
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(self.service().execute(CONSULT, "again"), "FAKE ANSWER")


# =========================================================================== V4
class V4_TelemetryNeverEscapesTheStateBoundary(Base):
    def test_home_equal_to_the_repository_writes_nothing_after_the_rejection(self):
        env = self.w.env(USERPROFILE=str(self.w.repo), HOME=str(self.w.repo))
        env.pop("GPT_ARCHITECT_V2_STATE_DIR")                 # the default now lies INSIDE the repository
        from bridge.service import ArchitectService

        svc = ArchitectService(env)
        before = sorted(p.relative_to(self.w.repo).as_posix() for p in self.w.repo.rglob("*")
                        if ".git" not in p.parts)
        exc = expect_error(self, svc)
        self.assertEqual(exc.code, "state_in_repo")
        after = sorted(p.relative_to(self.w.repo).as_posix() for p in self.w.repo.rglob("*")
                       if ".git" not in p.parts)
        self.assertEqual(after, before, "telemetry (or anything) was written into the reviewed repository")
        self.assertIsNone(svc._telemetry_dir())

    def test_a_valid_override_is_still_used(self):
        self.assertEqual(self.service()._telemetry_dir(), self.w.state)

    def test_an_invalid_override_falls_back_only_to_a_validated_default(self):
        bad = self.w.repo / "inside"
        svc = self.service(GPT_ARCHITECT_V2_STATE_DIR=str(bad))
        self.assertEqual(svc._telemetry_dir(), self.w.home / config.V2_STATE_DIRNAME)


# =========================================================================== V6
class V6_ExecutableSelection(Base):
    def _plant(self, directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"MZ planted\n")
        return path

    def test_a_git_in_the_repository_or_cwd_is_never_selected(self):
        ext = ".exe" if WIN else ""
        planted = self._plant(self.w.repo / "bin", "git" + ext)
        trusted_dir = self.w.tmp / "trusted"
        real = self._plant(trusted_dir, "git" + ext)
        env = {"PATH": os.pathsep.join([str(planted.parent), ".", "relative_dir", str(trusted_dir)])}
        found = execpath.trusted_which(("git",), env, exclude=[self.w.repo])
        self.assertEqual(Path(found).resolve(), real.resolve())
        # nothing trusted at all: None, never the planted one
        only_bad = {"PATH": str(planted.parent)}
        self.assertIsNone(execpath.trusted_which(("git",), only_bad, exclude=[self.w.repo]))

    def test_the_current_directory_is_never_searched(self):
        ext = ".exe" if WIN else ""
        cwd = self.w.tmp / "cwd"
        self._plant(cwd, "git" + ext)
        old = os.getcwd()
        os.chdir(cwd)
        try:
            self.assertIsNone(execpath.trusted_which(("git",), {"PATH": f".{os.pathsep}"}))
            self.assertIsNone(execpath.trusted_which(("git",), {"PATH": str(cwd)}))   # absolute but == cwd
        finally:
            os.chdir(old)

    def test_resolve_cli_does_not_pick_a_codex_shipped_by_the_project(self):
        planted = self._plant(self.w.repo, "codex.cmd")
        env = {"PATH": f"{self.w.repo}{os.pathsep}.", "CLAUDE_PROJECT_DIR": str(self.w.repo)}
        with self.assertRaises(errors.BridgeError) as cm:
            codexcli.resolve_cli(None, env)
        self.assertEqual(cm.exception.code, "not_on_path")
        self.assertTrue(planted.exists())
        # fail closed, but say WHY (a project that is the user's home would otherwise see a misleading error)
        self.assertIn("never trusted", str(cm.exception))
        self.assertIn("GPT_ARCHITECT_CODEX_PATH", str(cm.exception))

    def test_git_commands_use_an_absolute_trusted_executable_and_a_scrubbed_env(self):
        argv = gitsafe.safe_git_argv(self.w.repo, "status")
        self.assertTrue(os.path.isabs(argv[0]), argv[0])
        env = gitsafe.safe_git_env({"git_pager": "evil", "Git_External_Diff": "x", "GIT_DIR": "/elsewhere",
                                    "GIT_CONFIG_COUNT": "1", "Path": "p"})
        self.assertFalse([k for k in env if k.upper().startswith("GIT_") and k not in (
            "GIT_OPTIONAL_LOCKS", "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT", "GIT_PAGER")])
        self.assertEqual(env["GIT_PAGER"], "cat")
        self.assertEqual(env["NoDefaultCurrentDirectoryInExePath"], "1")

    @unittest.skipUnless(WIN, "system tools are Windows-only")
    def test_taskkill_and_tasklist_are_absolute_system_paths(self):
        for name in ("taskkill", "tasklist"):
            exe = execpath.system_tool(name)
            self.assertTrue(os.path.isabs(exe) and exe.lower().endswith(f"system32\\{name}.exe"), exe)
        seen = []
        real = subprocess.run

        def spy(argv, *a, **k):
            seen.append(argv[0])
            return real([sys.executable, "-c", "pass"], *a, **k)

        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with mock.patch.object(codexcli.subprocess, "run", spy):
                codexcli.kill_tree(proc)
        finally:
            proc.kill()
        self.assertTrue(any(os.path.isabs(s) for s in seen), seen)

    def test_no_trusted_git_makes_the_fingerprint_unavailable_not_unsafe(self):
        with mock.patch.object(gitsafe, "trusted_git", return_value=None):
            fp = fingerprint.repo_fingerprint(self.w.repo, dict(os.environ))
        self.assertFalse(fp["ok"])
        self.assertEqual(fp["reason"], "no_trusted_git")


# =========================================================================== V7
class V7_InfrastructureEvidenceIsComplete(Base):
    @staticmethod
    def item(**fields):
        return {"type": "command_execution", "status": "failed", "exit_code": 1, **fields}

    def test_signatures_in_nested_output_containers_are_found(self):
        _, kinds = runner.classify_item(self.item(result={"content": [{"type": "text", "text": HELPER}]}), None)
        self.assertIn("sandbox_setup", kinds)
        _, kinds = runner.classify_item(self.item(output={"a": {"b": {"c": [HELPER]}}}), None)
        self.assertIn("sandbox_setup", kinds)

    def test_the_command_that_was_asked_is_not_evidence(self):
        _, kinds = runner.classify_item(self.item(command=f"git grep '{HELPER}'", aggregated_output="no match"), None)
        self.assertEqual(kinds, [])

    def test_a_signature_in_the_middle_of_an_oversized_line_is_found(self):
        filler = "x" * (runner.MAX_LINE_BYTES * 2)
        line = ('{"type":"item.completed","item":{"type":"command_execution","status":"failed","aggregated_output":"'
                + filler + HELPER + filler + '"}}')
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"}, line,
                                    {"type": "turn.completed"}]})
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "turn_sandbox_setup_failed")
        self.assertEqual(self.w.telemetry()[0]["infra"]["oversize_signature_lines"], 1)

    def test_oversized_prose_is_not_flagged_but_an_unknown_oversized_record_is(self):
        filler = "y" * (runner.MAX_LINE_BYTES + 100)
        prose = ('{"type":"item.completed","item":{"type":"agent_message","text":"' + filler + HELPER + '"}}')
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"}, prose,
                                    {"type": "turn.completed"}]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.setUp()
        unknown = ('{"type":"item.completed","item":{"payload":{"deep":{"type":"x"}},"aggregated_output":"'
                   + filler + HELPER + '"}}')
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"}, unknown,
                                    {"type": "turn.completed"}]})
        self.assertEqual(expect_error(self, self.service()).code, "turn_sandbox_setup_failed")

    def test_the_oversize_scan_streams_every_byte_across_chunk_boundaries(self):
        scan = runner.OversizeScan(b"A" * 1000 + HELPER[:20].encode())
        scan.feed(HELPER[20:].encode() + b"B" * 1000)
        self.assertIn("sandbox_setup", scan.kinds)


# =========================================================================== V9
class V9_Maintenance(Base):
    def test_a_stale_flag_is_not_overwritten_by_a_new_maintenance_command(self):
        reg = self.registry()
        (self.w.state / "maintenance.json").write_text(json.dumps({"reason": "died", "owner_pid": 1}))
        with self.assertRaises(errors.BridgeError) as cm:
            reg.begin_maintenance("quarantine")
        self.assertEqual(cm.exception.code, "maintenance_stale")
        self.assertEqual(json.loads((self.w.state / "maintenance.json").read_text())["reason"], "died")
        rc, _, err = run_admin(self.w.env(), "quarantine", "--yes")
        self.assertEqual(rc, 1)
        self.assertIn("maintenance_stale", err)
        rc, out, _ = run_admin(self.w.env(), "end-maintenance", "--yes")
        self.assertEqual(rc, 0)
        reg.begin_maintenance("quarantine")          # now allowed
        reg.end_maintenance()

    def test_a_failing_unlink_still_releases_the_lock(self):
        reg = self.registry()
        reg.begin_maintenance("x")
        real_unlink = Path.unlink

        def deny(path, *a, **k):
            if path.name == "maintenance.json":
                raise PermissionError("denied")
            return real_unlink(path, *a, **k)

        with mock.patch.object(Path, "unlink", deny):
            self.assertFalse(reg.end_maintenance())
        other = state.Registry(self.w.state)
        with self.assertRaises(errors.BridgeError) as cm:
            other.begin_maintenance("y")
        self.assertEqual(cm.exception.code, "maintenance_stale", "the lock must be free (busy would mean leaked)")

    def test_a_failing_quiescence_probe_leaves_no_flag_and_no_lock(self):
        reg = self.registry()
        with mock.patch.object(state.Registry, "active_sessions", side_effect=OSError("probe failed")):
            with self.assertRaises(OSError):
                admin._require_quiescent(reg, "restore")
        self.assertFalse((self.w.state / "maintenance.json").exists())
        other = state.Registry(self.w.state)
        other.begin_maintenance("ok")
        other.end_maintenance()


# =========================================================================== V10
class V10_CapabilityRecheck(Base):
    def test_an_allow_listed_server_first_enabled_at_the_last_recheck_is_reported(self):
        self.w.set(mcp_sequence=[[], [], [{"name": "docs", "source": "config"}]])
        answer = self.service(GPT_ARCHITECT_MCP_ALLOW="docs").execute(CONSULT, "q")
        self.assertIn("docs", answer.splitlines()[1])
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["capabilities"]["mcp_allowed"], ["docs"])
        self.assertIn("mcp_allowed_servers", rec["capabilities"]["uncontained"])
        self.assertTrue(any(w.startswith("mcp_allow_list_active_late:docs") for w in rec["warnings"]))

    def test_the_recheck_follows_the_durable_marker_and_a_refusal_leaves_no_false_uncertainty(self):
        quiet = [{"name": "idea", "source": "config"}]
        self.w.set(mcp_sequence=[quiet, quiet, quiet + [{"name": "sneaky", "source": "config"}]])
        order: list[str] = []
        real_marker, real_recheck = state.Registry.write_marker, codexcli.recheck_mcp

        def marker(self_, kind, key, payload):
            if kind == "inflight":
                order.append("marker")
            return real_marker(self_, kind, key, payload)

        def recheck(*a, **k):
            order.append("recheck")
            return real_recheck(*a, **k)

        with mock.patch.object(state.Registry, "write_marker", marker), \
                mock.patch.object(codexcli, "recheck_mcp", recheck):
            exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "mcp_changed_before_exec")
        self.assertLess(order.index("marker"), order.index("recheck"),
                        "the last read before exec must come AFTER the durable in-flight record")
        self.assertFalse(exc.inference_may_have_started)
        reg = state.Registry(self.w.state)
        self.assertIsNone(reg.read_marker("inflight", self.key()), "a refusal ran nothing: no false uncertainty")
        rec = self.w.telemetry()[0]
        self.assertFalse(rec["inference_may_have_started"])
        self.assertEqual(rec["history"], "no_turn_started")
        self.w.set(mcp_sequence=[quiet])
        self.assertEqual(self.service().execute(CONSULT, "again"), "FAKE ANSWER")


# =========================================================================== V12
class V12_FingerprintReadCompleteness(Base):
    def _git(self, *args):
        return self.w.git(*args)

    def test_a_reader_exception_makes_the_capture_incomplete(self):
        class FakeStdout:
            def read(self, n):
                raise OSError("pipe broke")

            def close(self):
                pass

        class FakeProc:
            stdout = FakeStdout()
            returncode = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        with mock.patch.object(fingerprint.subprocess, "Popen", return_value=FakeProc()):
            rc, data, complete = fingerprint._run_capture(["git"], {}, 5)
        self.assertFalse(complete)

    def test_an_incomplete_head_read_is_unavailable_not_unborn(self):
        real = fingerprint._run_capture
        calls = {"n": 0}

        def flaky(argv, env, timeout_s, hasher=None, cap=None):
            calls["n"] += 1
            if "rev-parse" in argv and any(str(a).startswith("HEAD") for a in argv):
                return 1, b"", False
            return real(argv, env, timeout_s, hasher, cap)

        with mock.patch.object(fingerprint, "_run_capture", flaky):
            fp = fingerprint.repo_fingerprint(self.w.repo, dict(os.environ))
        self.assertFalse(fp["ok"])
        self.assertEqual(fp["reason"], "head_incomplete")

    def test_a_head_whose_object_is_missing_is_not_called_unborn(self):
        head = self.w.repo / ".git" / "HEAD"
        head.write_text("f" * 40 + "\n")                    # detached HEAD at an object that does not exist
        fp = fingerprint.repo_fingerprint(self.w.repo, dict(os.environ))
        self.assertFalse(fp["ok"])
        self.assertIn(fp["reason"], ("head_unverifiable", "head_failed", "head_unreadable"))

    def test_a_verified_unborn_repository_is_still_fingerprinted(self):
        d = Path(tempfile.mkdtemp(prefix="unborn_", dir=self.w.tmp))
        subprocess.run([gitsafe.trusted_git(d), "-C", str(d), "init", "-q"], check=True)
        (d / "a.txt").write_text("x")
        fp = fingerprint.repo_fingerprint(d, dict(os.environ))
        self.assertTrue(fp["ok"])
        self.assertEqual(fp["head"], "UNBORN")


# =========================================================================== V13
class V13_EvidenceBinding(unittest.TestCase):
    def test_run_problems_rejects_a_changed_tree_a_nonzero_exit_and_skips(self):
        from tools import record_evidence as rec

        ok = dict(before="a", after="a", exit_code=0, skipped=0, failing=[], tests_run=5)
        self.assertEqual(rec.run_problems(**ok), [])
        self.assertTrue(any("changed while the tests ran" in p for p in rec.run_problems(**{**ok, "after": "b"})))
        self.assertTrue(any("exited 1" in p for p in rec.run_problems(**{**ok, "exit_code": 1})))
        self.assertTrue(any("skipped" in p for p in rec.run_problems(**{**ok, "skipped": 2})))
        self.assertTrue(any("failing" in p for p in rec.run_problems(**{**ok, "failing": ["t"]})))
        self.assertTrue(any("no test count" in p for p in rec.run_problems(**{**ok, "tests_run": None})))

    def test_the_prompt_gate_checks_after_run_hash_exit_code_skips_and_the_retained_log(self):
        from tools import make_verification_prompt as mvp

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ev = root / "docs" / "evidence"
            ev.mkdir(parents=True)
            sha = "a" * 64
            (ev / "source_fingerprint.json").write_text(json.dumps({"combined_sha256": sha}))
            (ev / "capability_policy_real_cli.json").write_text(json.dumps({"source_combined_sha256": sha}))
            log = b"Ran 5 tests in 1.0s\n\nOK\n"
            (ev / "tests_x.log").write_bytes(log)
            for name in ("integrity.json", "cli_observations.json", "prefix_check.json"):
                (ev / name).write_text(json.dumps({"source_combined_sha256": sha}))
            good = {"source_combined_sha256": sha, "source_sha256_after_run": sha, "outcome": "OK", "failing": [],
                    "tests_run": 5, "exit_code": 0, "skipped": 0, "raw_log": "tests_x.log",
                    "raw_log_sha256": hashlib.sha256(log).hexdigest(), "executed_from": "staged_copy",
                    "source_sha256_staged_copy": sha}

            def check(**changes):
                (ev / "tests_x.json").write_text(json.dumps({**good, **changes}))
                return mvp.evidence_problems(root, sha)

            self.assertEqual(check(), [])
            self.assertTrue(any("tree changed" in p for p in check(source_sha256_after_run="b" * 64)))
            self.assertTrue(any("no after-run" in p or "tree changed" in p for p in check(source_sha256_after_run=None)))
            self.assertTrue(any("exit 3" in p for p in check(exit_code=3)))
            self.assertTrue(any("skipped" in p for p in check(skipped=1)))
            self.assertTrue(any("raw test output does not match" in p for p in check(raw_log_sha256="0" * 64)))
            (ev / "tests_x.log").unlink()
            self.assertTrue(any("raw test output is missing" in p for p in check()))


class V13_NothingLocalLeavesTheMachine(unittest.TestCase):
    """Evidence is embedded in a prompt sent to a third party: no raw MCP configuration, no profile
    path, no account name."""

    def test_scrub_replaces_the_profile_path_in_every_slash_style_and_the_account_name(self):
        from tools import privacy

        home, user = r"C:\Users\someone", "someone"
        text = (r"C:\Users\someone\AppData\x | C:/Users/someone/y | C:\\Users\\someone\\z | "
                "owner SOMEONE | unrelated text")
        out = privacy.scrub_local(text, home, user)
        for needle in ("someone", "SOMEONE", "Users"):
            self.assertNotIn(needle, out)
        self.assertIn("unrelated text", out)
        self.assertTrue(privacy.contains_local(text, home, user))
        self.assertFalse(privacy.contains_local("nothing here", home, user))

    def test_mcp_list_is_normalised_to_names_and_enabled_flags_only(self):
        from tools import record_evidence as rec

        raw = json.dumps([
            {"name": "zeta", "enabled": True, "transport": {"type": "stdio", "command": r"C:\Users\x\node_repl.exe",
                                                            "env": {"TOKEN": "s3cret", "PIPE": r"\\.\pipe\abc"}}},
            {"name": "alpha", "enabled": False, "auth_status": "unsupported"},
        ])
        rows = rec.normalise_mcp_list(raw)
        self.assertEqual(rows, [{"name": "alpha", "enabled": False}, {"name": "zeta", "enabled": True}])
        self.assertNotIn("s3cret", json.dumps(rows))
        self.assertIsNone(rec.normalise_mcp_list("not json"))
        self.assertIsNone(rec.normalise_mcp_list('{"a": 1}'))
        self.assertIsNone(rec.normalise_mcp_list('[{"enabled": true}]'))

    def test_the_prompt_builder_embeds_scrubbed_text_and_flags_it(self):
        from tools import make_verification_prompt as mvp, privacy

        home, user = privacy.identifiers()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in [*mvp.FULL, *mvp.DIFF_ONLY]:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"plain content of {rel}\n", encoding="utf-8")
            leaky = root / "bridge" / "redact.py"
            leaky.write_text(f"profile {home}\\AppData and account {user}\n", encoding="utf-8")
            old = root / mvp.SNAPSHOT / mvp.DIFF_ONLY[0]
            old.parent.mkdir(parents=True)
            old.write_text(f"old {home}\n", encoding="utf-8")
            prompt, manifest = mvp.assemble(root)
        for needle in (home, user):
            self.assertNotIn(needle.lower(), prompt.lower())
        entry = next(f for f in manifest["files"] if f["path"] == "bridge/redact.py")
        self.assertTrue(entry["scrubbed_local_identifiers"])
        self.assertIn("<home>", prompt)


# =========================================================================== V14
class V14_WarningsAreNeverHidden(Base):
    def test_details_precede_the_answer(self):
        self.w.set(mcp_servers=[])
        answer = self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0").execute(CONSULT, "q")
        lines = answer.split("\n")
        self.assertTrue(lines[0].startswith("[bridge-warning] 1 warning(s)"), lines[0])
        self.assertIn("DISABLED", lines[1])
        self.assertTrue(answer.endswith("FAKE ANSWER"), "the answer follows the warning block")

    def test_a_disabled_preflight_is_visible_to_the_caller(self):
        answer = self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0").execute(CONSULT, "q")
        self.assertIn("preflight is DISABLED", answer)
        self.assertIn("sandbox_preflight_skipped:disabled", self.w.telemetry()[0]["warnings"])

    def test_every_recorded_warning_has_caller_visible_text_even_if_a_call_site_forgets(self):
        """A future call site that records a warning without text must still be rendered."""
        with mock.patch.object(service._Ctx, "warn",
                               lambda ctx, code, banner: ctx.warnings.append(code)):
            answer = self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0").execute(CONSULT, "q")
        self.assertIn("Recorded warning: sandbox_preflight_skipped:disabled.", answer)

    def test_unavailable_post_turn_recheck_and_reaped_descendants_are_bannered(self):
        real = codexcli.recheck_mcp
        calls = {"n": 0}

        def second_fails(*a, **k):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise errors.BridgeError(errors.CAPABILITY_AUDIT, "unreadable", code="mcp_list_unreadable")
            return real(*a, **k)

        with mock.patch.object(codexcli, "recheck_mcp", second_fails):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("could not be re-read after the turn", answer)
        self.assertIn("mcp_recheck_unavailable", self.w.telemetry()[0]["warnings"])

    def test_there_is_no_prefix_exemption_left(self):
        """R12: a code is rendered only when its banner was actually produced (exact codes)."""
        self.assertFalse(hasattr(service, "_BANNERED_ELSEWHERE"))


# =========================================================================== V15
class V15_ExactFormats(unittest.TestCase):
    def test_thread_ids_must_match_in_full(self):
        for bad in (THREAD + "\n", "\n" + THREAD, THREAD + " ", "x" * 7, THREAD + "\r\n"):
            self.assertFalse(ids.is_thread_id(bad), repr(bad))
        self.assertTrue(ids.is_thread_id(THREAD))

    def test_a_thread_started_event_with_a_trailing_newline_is_dropped_at_ingress(self):
        ev = runner.Events()
        ev.feed(json.dumps({"type": "thread.started", "thread_id": THREAD + "\n"}).encode())
        self.assertIsNone(ev.thread_id)
        self.assertTrue(ev.thread_invalid)

    def test_telemetry_does_not_restore_a_malformed_thread_id(self):
        rec = telemetry.sanitise({"session": {"thread_id": THREAD + "\n", "name": "default"}})
        self.assertIsNone(rec["session"]["thread_id"])

    def test_typed_fields_are_checked_against_their_own_schema(self):
        rec = telemetry.sanitise({"call_id": "ab" * 16, "tool": CONSULT, "outcome": "totally-made-up",
                                  "error_category": "timeout", "error_code": "has space", "cli_version": "0.162.1",
                                  "pointer_state": "elsewhere", "history": "turn_may_exist"})
        self.assertEqual(rec["outcome"], None)
        self.assertEqual(rec["pointer_state"], None)
        self.assertEqual(rec["error_code"], None)
        self.assertEqual((rec["call_id"], rec["tool"], rec["error_category"], rec["history"], rec["cli_version"]),
                         ("ab" * 16, CONSULT, "timeout", "turn_may_exist", "0.162.1"))
        self.assertIsNone(telemetry.sanitise({"started_at": "2026-10-12T10:00:00Z\n"})["started_at"])

    def test_admin_model_and_session_arguments_must_match_in_full(self):
        """The call sites, not just the patterns: `$` accepted a trailing newline through `.match`."""
        from tests.helpers import World

        w = World()
        try:
            env = w.env()
            for argv, code in ((["adopt", "--thread", THREAD, "--model", "gpt-6.1-sol\n", "--yes"], "bad_model"),
                               (["new", "--session", "default\n", "--model", "gpt-6.1-sol"], "bad_session")):
                rc, out, err = run_admin(env, *argv)
                self.assertEqual(rc, 1, (argv, out, err))
                self.assertIn(code, err)
            self.assertFalse((w.state / "registry.json").exists(), "nothing may be created for a rejected name")
            rc, out, err = run_admin(env, "new", "--session", "default", "--model", "gpt-6.1-sol")
            self.assertEqual(rc, 0, err)
        finally:
            w.cleanup()


if __name__ == "__main__":
    unittest.main()
