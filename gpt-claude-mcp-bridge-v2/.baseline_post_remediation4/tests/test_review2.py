"""Regression tests for the second remediation (GPT review findings N1-N15 and P3).

Everything runs against the scripted fake CLI (a real child process) or real git; no model.
Each test names the finding it pins.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, WorldCase, pid_alive, wait_for

from bridge import admin, codexcli, config, errors, fingerprint, gitsafe, jobobject, runner, state, telemetry

CONSULT, REVIEW = config.CONSULT, config.REVIEW
WIN = sys.platform == "win32"
GOOD_THREAD = "aaaaaaaa-1111-2222-3333-444444444444"
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


def thread_started():
    return {"type": "thread.started", "thread_id": "{thread}"}


def completed():
    return {"type": "turn.completed"}


def cmd_item(output, status="failed", exit_code=1, cwd=None):
    item = {"id": "i1", "type": "command_execution", "command": "x", "aggregated_output": output,
            "exit_code": exit_code, "status": status}
    if cwd:
        item["cwd"] = cwd
    return {"type": "item.completed", "item": item}


# =========================================================================== N1
class N1_HostGitContainment(WorldCase):
    """Host-side git must not run helpers selected by repository attributes / config."""

    KINDS = ("textconv", "clean", "fsmonitor")

    def _fired(self) -> set[str]:
        return {k for k in self.KINDS if (self.w.tmp / f"fired_{k}.txt").exists()}

    def _hostile_repo(self) -> None:
        """A repository whose worktree attributes and config select three helper programs.

        The helpers are one script outside the repository.  (Inline `sh -c '...; ...'` values do not
        work: `;` and `#` start a comment in a git config value, which truncates the command.)"""
        helper = self.w.tmp / "helper.sh"
        if " " in helper.as_posix():
            self.skipTest(f"cannot build the control: temporary path contains a space ({helper})")
        helper.write_text('#!/bin/sh\nkind="$1"\n'
                          f'echo "$kind" >> "{self.w.tmp.as_posix()}/fired_$kind.txt"\n'
                          'case "$kind" in\n  clean) cat ;;\n  textconv) cat "$2" ;;\n  *) exit 0 ;;\nesac\n',
                          encoding="utf-8", newline="\n")
        repo = self.w.repo
        (repo / ".gitattributes").write_text("*.txt diff=evil filter=evil\n")
        (repo / "doc.txt").write_text("one\n")
        self.w.git("add", "-A")
        self.w.git("-c", "user.email=t@e.i", "-c", "user.name=t", "commit", "-q", "-m", "files")
        h = helper.as_posix()
        with open(repo / ".git" / "config", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f'[diff "evil"]\n\ttextconv = sh {h} textconv\n'
                     f'[filter "evil"]\n\tclean = sh {h} clean\n'
                     f'[core]\n\tfsmonitor = sh {h} fsmonitor\n')
        (repo / "doc.txt").write_text("two\n")             # modified: diff/status must consult the helpers

    def test_helpers_fire_with_plain_git_but_never_through_gitsafe(self):
        self._hostile_repo()
        subprocess.run(["git", "-C", str(self.w.repo), "diff", "HEAD"], capture_output=True)
        subprocess.run(["git", "-C", str(self.w.repo), "status", "--porcelain"], capture_output=True)
        control = self._fired()
        # The control must be valid: if nothing fired, a clean result below would prove nothing.
        self.assertTrue({"textconv", "clean"} <= control,
                        f"control invalid: plain git ran only {sorted(control)} of {list(self.KINDS)}")
        for k in control:
            (self.w.tmp / f"fired_{k}.txt").unlink()

        fp = fingerprint.repo_fingerprint(self.w.repo, dict(os.environ))
        self.assertTrue(fp["ok"], fp)
        probe, why = codexcli._probe_file(self.w.repo, dict(os.environ))
        self.assertEqual(self._fired(), set(), "a repository-selected helper ran host-side")
        self.assertIn(probe, ("README.md", "doc.txt"))

    def test_the_control_test_fails_when_containment_is_removed(self):
        """Mutation check: with plain `git -C <root> ...` substituted for the contained argv, the
        control test above must FAIL; otherwise it would pass for the wrong reason."""
        script = (
            "import os, sys, unittest\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from bridge import gitsafe\n"
            "gitsafe.safe_git_argv = lambda root, *a: ['git', '-C', str(root), *a]\n"
            "from tests.test_review2 import N1_HostGitContainment as C\n"
            "suite = unittest.TestSuite([C('test_helpers_fire_with_plain_git_but_never_through_gitsafe')])\n"
            "with open(os.devnull, 'w') as sink:\n"
            "    res = unittest.TextTestRunner(stream=sink).run(suite)\n"
            "sys.exit(77 if res.skipped else (0 if res.failures else 3))\n"
        )
        done = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=180)
        if done.returncode == 77:
            self.skipTest("the control could not be built on this machine")
        self.assertEqual(done.returncode, 0,
                         f"the control test did NOT fail with containment removed (rc={done.returncode}): "
                         f"{done.stderr[-400:]}")

    def test_safe_argv_carries_every_isolation_control(self):
        argv = gitsafe.safe_git_argv("R:/repo", "status")
        joined = " ".join(argv)
        for needle in ("--no-optional-locks", f"--attr-source={gitsafe.EMPTY_TREE}", "core.fsmonitor=false",
                       "diff.external=", "core.attributesFile=", "-C R:/repo"):
            self.assertIn(needle, joined)
        env = gitsafe.safe_git_env({"GIT_EXTERNAL_DIFF": "evil", "GIT_PAGER": "less", "PATH": "p"})
        self.assertNotIn("GIT_EXTERNAL_DIFF", env)
        self.assertEqual((env["GIT_OPTIONAL_LOCKS"], env["GIT_PAGER"], env["PATH"]), ("0", "cat", "p"))
        self.assertTrue(gitsafe.supported())

    def test_fingerprint_diff_calls_disable_textconv_and_external_diff(self):
        seen: list[list[str]] = []
        real = fingerprint._run_capture

        def spy(argv, *a, **k):
            seen.append(list(argv))
            return real(argv, *a, **k)

        with mock.patch.object(fingerprint, "_run_capture", spy):
            fingerprint.repo_fingerprint(self.w.repo, dict(os.environ))
        diffs = [a for a in seen if "diff" in a]
        self.assertEqual(len(diffs), 2)
        for a in diffs:
            self.assertIn("--no-textconv", a)
            self.assertIn("--no-ext-diff", a)

    def test_fingerprint_and_probe_do_not_modify_anything_under_dot_git(self):
        def snap():
            out = {}
            for p in (self.w.repo / ".git").rglob("*"):
                if p.is_file():
                    out[str(p.relative_to(self.w.repo))] = (p.stat().st_size, p.stat().st_mtime_ns)
            return out

        (self.w.repo / "touch.txt").write_text("x")      # untracked: status must not refresh the index
        before = snap()
        fingerprint.repo_fingerprint(self.w.repo, dict(os.environ))
        codexcli._probe_file(self.w.repo, dict(os.environ))
        self.assertEqual(snap(), before, "host-side git changed files under .git (recursive check)")


# =========================================================================== N2
class N2_StateDirValidationEverywhere(WorldCase):
    def _env(self, state_dir: Path) -> dict[str, str]:
        return self.w.env(GPT_ARCHITECT_V2_STATE_DIR=str(state_dir))

    def test_registry_wide_commands_reject_a_state_dir_inside_the_repository(self):
        bad = self.w.repo / "stateinrepo"
        bad.mkdir()
        (bad / "registry.json").write_text('{"schema_version": 1, "sessions": {}, "retired": []}')
        before = (bad / "registry.json").read_bytes()
        for cmd in ("quarantine", "reinit", "restore-prev", "end-maintenance"):
            rc, _, err = run_admin(self._env(bad), cmd, "--yes", "--repo", str(self.w.repo))
            self.assertEqual(rc, 1, cmd)
            self.assertIn("state_in_repo", err, cmd)
        self.assertEqual((bad / "registry.json").read_bytes(), before, "registry in the repo must be untouched")
        self.assertEqual(sorted(p.name for p in bad.iterdir()), ["registry.json"],
                         "nothing may be created in the rejected directory (no locks/, no flags)")

    def test_registry_wide_commands_reject_an_overlap_with_v1_even_without_a_repo(self):
        v1 = self.w.home / config.V1_STATE_DIRNAME
        v1.mkdir()
        (v1 / "abc.json").write_text("{}")
        for target in (v1, v1 / "nested"):
            for cmd in ("quarantine", "reinit", "restore-prev", "end-maintenance"):
                env = self._env(target)
                env["CLAUDE_PROJECT_DIR"] = str(self.w.tmp)          # not a repository
                rc, _, err = run_admin(env, cmd, "--yes")
                self.assertEqual(rc, 1, (target, cmd))
                self.assertIn("state_overlaps_v1", err)
        self.assertEqual(sorted(p.name for p in v1.iterdir()), ["abc.json"], "v1 state must never be touched")

    def test_status_with_a_rejected_state_dir_probes_nothing(self):
        bad = self.w.repo / "stateinrepo2"
        rc, out, _ = run_admin(self._env(bad), "status", "--json")
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertEqual(report["registry"]["error"], "state_dir_rejected")
        self.assertTrue(any("state_in_repo" in p for p in report["problems"]))
        self.assertFalse(bad.exists(), "a rejected state directory must not be created, locked or probed")

    def test_session_commands_validate_too(self):
        bad = self.w.repo / "stateinrepo3"
        for cmd in (["new"], ["reset", "--yes"], ["reconcile", "--yes"], ["adopt", "--thread", GOOD_THREAD,
                                                                           "--model", "gpt-6.1-sol", "--yes"]):
            rc, _, err = run_admin(self._env(bad), *cmd)
            self.assertEqual(rc, 1, cmd)
            self.assertIn("state_in_repo", err)
        self.assertFalse(bad.exists())


# =========================================================================== N3 / N4
@unittest.skipUnless(WIN, "Job Objects / suspended start are Windows-only")
class N3N4_OwnershipAndStaleRecovery(WorldCase):
    def _key(self):
        root, repo_key = state.canonical_repo(str(self.w.repo))
        return state.session_key(repo_key, "gpt-6.1-sol", "default")

    def test_child_is_assigned_to_the_job_before_it_can_run(self):
        started = self.w.tmp / "started"
        self.w.set(exec={"started_file": str(started)})
        seen = {}
        real_resume = jobobject.ProcessOwnership.resume

        def resume(self, proc):
            seen["ran_before_resume"] = started.exists()
            seen["assigned"] = self.owned
            return real_resume(self, proc)

        with mock.patch.object(jobobject.ProcessOwnership, "resume", resume):
            self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(seen, {"ran_before_resume": False, "assigned": True})
        self.assertTrue(started.exists())

    def test_resume_failure_refuses_without_running_anything(self):
        started = self.w.tmp / "started"
        self.w.set(exec={"started_file": str(started)})
        with mock.patch.object(jobobject.ProcessOwnership, "resume", lambda self, proc: False):
            exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "ownership_unavailable")
        self.assertFalse(started.exists())
        self.assertFalse(exc.inference_may_have_started)

    def test_setup_exception_after_attach_kills_the_child_and_blocks_the_session(self):
        started = self.w.tmp / "started"
        self.w.set(exec={"started_file": str(started), "sleep": 30})
        with mock.patch.object(jobobject.ProcessOwnership, "resume",
                               mock.Mock(side_effect=RuntimeError("boom after attach"))):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SUBPROCESS_FAILED, "runner_failed"))
        self.assertFalse(started.exists(), "the suspended child must be dead, never resumed")
        marker = state.Registry(self.w.state).read_marker("inflight", self._key())
        self.assertEqual((marker["state"], marker["reason"]), ("uncertain", "runner_failed"))
        # N4: a later call must NOT auto-clear this as "the OS closed the job"
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")

    def _write_stale(self, **overrides):
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        marker = {"state": "running", "phase": "running", "call_id": "c" * 8, "owner_pid": 2_000_000_000,
                  "owner_identity": "1", "thread_id": GOOD_THREAD, "ownership": "job_object", "resumed": False}
        marker.update(overrides)
        reg.write_marker("inflight", self._key(), marker)
        return reg

    def test_stale_marker_with_a_live_owner_is_never_auto_cleared(self):
        self._write_stale(owner_pid=os.getpid(), owner_identity=state.process_identity(os.getpid()))
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "stale_inflight_owner_alive"))
        self.assertEqual(self.w.exec_entries(), [])

    def test_a_dead_owner_or_reused_pid_is_recognised_as_dead_but_never_auto_recovered(self):
        """The owner is provably gone (so no 'owner alive' refusal), yet V3 still requires the
        operator: the turn may have run and sandbox-service commands are outside the job."""
        self._write_stale()                                  # pid that cannot exist
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        self.setUp()
        # same PID as a live process but a different creation time => PID reuse => owner is dead
        self._write_stale(owner_pid=os.getpid(), owner_identity="123")
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "uncertain_termination")
        self.assertNotEqual(exc.code, "stale_inflight_owner_alive")

    def test_unowned_dead_owner_stays_in_doubt(self):
        self._write_stale(ownership="none")
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")

    def test_owner_liveness_never_reads_a_failed_query_as_death(self):
        """N4: only a PID that verifiably does not exist (or has exited) is dead."""
        me = state.process_identity(os.getpid())
        self.assertTrue(me and me.isdigit())
        self.assertTrue(state.owner_is_alive(os.getpid(), me))
        self.assertFalse(state.owner_is_alive(os.getpid(), "123"), "reused PID: different creation time")
        self.assertIsNone(state.process_identity(2_000_000_000))
        self.assertFalse(state.owner_is_alive(2_000_000_000, "1"))
        # PID 4 (System) exists but a normal user usually cannot read it: that is "alive/unknown",
        # never "dead" (before the fix an access-denied OpenProcess returned None here)
        self.assertIsNotNone(state.process_identity(4))
        self.assertTrue(state.owner_is_alive(4, None))

    def test_unqueryable_identity_counts_as_alive_in_either_position(self):
        with mock.patch.object(state, "process_identity", return_value=state.UNQUERYABLE):
            self.assertTrue(state.owner_is_alive(1234, "999"))
            self.assertTrue(state.owner_is_alive(1234, None))
        with mock.patch.object(state, "process_identity", return_value="555"):
            self.assertTrue(state.owner_is_alive(1234, state.UNQUERYABLE))
            self.assertFalse(state.owner_is_alive(1234, "999"))

    def test_an_exited_process_whose_handle_is_still_open_is_dead(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        try:
            proc.wait(30)                        # exited; Popen still holds the process handle open
            self.assertIsNone(state.process_identity(proc.pid))
            self.assertFalse(state.owner_is_alive(proc.pid, None))
        finally:
            proc.kill() if proc.poll() is None else None

    def test_completed_but_unbound_turn_keeps_both_process_and_thread_debt(self):
        self._write_stale(phase="completed_unbound")
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"))
        self.assertIn(GOOD_THREAD, str(exc))
        reg = state.Registry(self.w.state)
        self.assertEqual(reg.read_marker("inflight", self._key())["state"], "uncertain")
        self.assertEqual(reg.read_marker("pending", self._key())["thread_id"], GOOD_THREAD)
        self.assertEqual(self.w.exec_entries(), [])


# =========================================================================== N3 (accounting lag)
@unittest.skipUnless(WIN, "Job Objects are Windows-only")
class N3_ExitAccountingLag(WorldCase):
    """Job accounting still shows members for a few ms after a normal exit (measured ~4 ms for a
    launcher + interpreter pair).  That must not be reported as a descendant that outlived the CLI."""

    def test_transient_count_after_a_normal_exit_is_not_a_survivor(self):
        counts = iter([1, 1, 1])                           # lagging accounting, then drained

        def lagging(owner):
            return next(counts, 0)

        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", lagging):
            answer = self.service().execute(CONSULT, "q")
        self.assertEqual(answer, "FAKE ANSWER")
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["process"]["reaped_descendants"], 0)
        self.assertEqual(rec["process"]["termination"], "not_needed")
        self.assertFalse(any(w.startswith("reaped_descendants") for w in rec["warnings"]), rec["warnings"])

    def test_a_count_that_persists_past_the_grace_window_is_still_a_survivor(self):
        with mock.patch.object(runner, "EXIT_ACCOUNTING_GRACE_S", 0.05), \
                mock.patch.object(jobobject.ProcessOwnership, "active_processes", lambda owner: 1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda owner: None):
            self.service().execute(CONSULT, "q")
        rec = self.w.telemetry()[0]
        self.assertEqual((rec["process"]["reaped_descendants"], rec["process"]["termination"]), (1, "unconfirmed"))
        self.assertEqual(rec["outcome"], "success_session_in_doubt")


# =========================================================================== N5
class N5_ExclusiveMaintenance(WorldCase):
    def test_a_second_maintenance_owner_can_neither_start_nor_clear_the_first(self):
        a, b = state.Registry(self.w.state), state.Registry(self.w.state)
        a.ensure_dirs()
        a.begin_maintenance("admin A")
        with self.assertRaises(errors.BridgeError) as cm:
            b.begin_maintenance("admin B")
        self.assertEqual(cm.exception.code, "maintenance_busy")
        b.end_maintenance()                                   # not the owner: must not clear A's flag
        self.assertEqual(a.maintenance()["reason"], "admin A")
        with self.assertRaises(errors.BridgeError):
            b.end_stale_maintenance()                         # A is alive and holds the lock
        a.end_maintenance()
        self.assertIsNone(a.maintenance())

    def test_exactly_one_of_many_concurrent_admins_wins(self):
        reg0 = state.Registry(self.w.state)
        reg0.ensure_dirs()
        n = 6
        barrier = threading.Barrier(n)
        results: list[str] = []
        regs = [state.Registry(self.w.state) for _ in range(n)]

        def go(r):
            barrier.wait()
            try:
                r.begin_maintenance("race")
                results.append("won")
                time.sleep(0.4)                               # hold it while the others fail
                r.end_maintenance()
            except errors.BridgeError:
                results.append("lost")

        threads = [threading.Thread(target=go, args=(r,)) for r in regs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual((results.count("won"), results.count("lost")), (1, n - 1), results)
        self.assertIsNone(reg0.maintenance())

    def test_session_mutations_refuse_while_registry_wide_maintenance_is_active(self):
        self.service().execute(CONSULT, "q")
        before = (self.w.state / "registry.json").read_bytes()
        thread = next(iter(self.w.registry()["sessions"].values()))["thread_id"]
        holder = state.Registry(self.w.state)
        holder.begin_maintenance("quarantine in progress")
        try:
            for cmd in (["new", "--session", "other"], ["reset", "--yes"], ["reconcile", "--yes"],
                        ["adopt", "--thread", GOOD_THREAD, "--model", "gpt-6.1-sol", "--yes"]):
                rc, _, err = run_admin(self.w.env(), *cmd)
                self.assertEqual(rc, 1, cmd)
                self.assertIn("state_maintenance", err, cmd)
            self.assertEqual(expect_error(self, self.service()).code, "state_maintenance")
        finally:
            holder.end_maintenance()
        self.assertEqual((self.w.state / "registry.json").read_bytes(), before)
        self.assertEqual(next(iter(self.w.registry()["sessions"].values()))["thread_id"], thread)

    def test_a_stale_flag_blocks_calls_until_the_operator_clears_it(self):
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        (self.w.state / "maintenance.json").write_text(json.dumps({"reason": "died", "owner_pid": 1}))
        self.assertEqual(expect_error(self, self.service()).code, "state_maintenance")
        rc, out, _ = run_admin(self.w.env(), "end-maintenance", "--yes")
        self.assertEqual(rc, 0)
        self.assertIn("cleared", out)
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")


# =========================================================================== N6
class N6_DurableAdmissionAndPersistence(WorldCase):
    def test_no_inference_without_a_persisted_inflight_marker(self):
        real = state.Registry.write_marker

        def fail_inflight(self, kind, key, payload):
            return False if kind == "inflight" else real(self, kind, key, payload)

        with mock.patch.object(state.Registry, "write_marker", fail_inflight):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.STATE_ERROR, "inflight_marker_unwritable"))
        self.assertFalse(exc.inference_may_have_started)
        self.assertEqual(self.w.exec_entries(), [])

    def test_a_spawn_failure_ran_nothing_and_does_not_block_the_session(self):
        real = subprocess.Popen

        def fail_exec_only(argv, *a, **k):
            if "exec" in list(argv):
                raise OSError("simulated: the CLI could not be started")
            return real(argv, *a, **k)

        with mock.patch.object(subprocess, "Popen", fail_exec_only):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SUBPROCESS_FAILED, "spawn_failed"))
        self.assertFalse(exc.inference_may_have_started)
        rec = self.w.telemetry()[0]
        self.assertFalse(rec["inference_may_have_started"])
        self.assertEqual(rec["history"], "no_turn_started")
        self.assertEqual(self.w.exec_entries(), [])
        # nothing ran: no in-doubt state, the next call simply works
        self.assertEqual(self.service().execute(CONSULT, "works now"), "FAKE ANSWER")

    def test_inflight_marker_is_kept_until_the_pointer_is_committed(self):
        seen = {}
        real = state.Registry.bind_thread

        def spy(self, key, ident, thread_id, cli_version):
            m = self.read_marker("inflight", key)
            seen["marker_during_bind"] = m is not None
            seen["phase"] = (m or {}).get("phase")
            return real(self, key, ident, thread_id, cli_version)

        with mock.patch.object(state.Registry, "bind_thread", spy):
            self.service().execute(CONSULT, "q")
        self.assertEqual(seen, {"marker_during_bind": True, "phase": "completed_unbound"})
        root, repo_key = state.canonical_repo(str(self.w.repo))
        key = state.session_key(repo_key, "gpt-6.1-sol", "default")
        self.assertIsNone(state.Registry(self.w.state).read_marker("inflight", key))

    def test_crash_between_verification_and_binding_never_allows_a_silent_second_thread(self):
        class Crash(BaseException):
            pass

        def die(self, *a, **k):
            raise Crash()

        with mock.patch.object(state.Registry, "bind_thread", die):
            with self.assertRaises(Crash):
                self.service().execute(CONSULT, "q")
        root, repo_key = state.canonical_repo(str(self.w.repo))
        key = state.session_key(repo_key, "gpt-6.1-sol", "default")
        reg = state.Registry(self.w.state)
        marker = reg.read_marker("inflight", key)
        self.assertEqual(marker["phase"], "completed_unbound")
        # the bridge process is "restarted": the recorded owner is gone
        marker.update(owner_pid=2_000_000_000, owner_identity="1")
        reg.write_marker("inflight", key, marker)
        exc = expect_error(self, self.service())
        # R2: a dead owner is never recovered on the strength of a completed phase alone - the process
        # fact needs the operator's reconcile; the thread debt is a separate, durable pending marker
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"))
        self.assertEqual(len(self.w.exec_entries()), 1, "no second thread may be started")
        self.assertEqual((reg.read_marker("pending", key) or {}).get("thread_id"), marker["thread_id"])

    def test_instance_file_write_failure_is_a_partial_success_not_an_internal_error(self):
        real = state.atomic_write_text

        def flaky(path, text):
            if Path(path).name == "instance.json":
                raise OSError("simulated disk full")
            return real(path, text)

        with mock.patch.object(state, "atomic_write_text", flaky):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("PARTIAL SUCCESS", answer)
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["outcome"], "success_partial_persistence")
        self.assertNotEqual(rec["error_category"], errors.INTERNAL)
        self.assertEqual(expect_error(self, self.service()).code, "pending_persistence")

    def test_resumed_banner_only_claims_the_pointer_when_a_fresh_read_proves_it(self):
        svc = self.service()
        svc.execute(CONSULT, "seed")
        real_get, real_bind = state.Registry.get, state.Registry.bind_thread
        calls = {"n": 0}

        def get(self, key, ident):
            calls["n"] += 1
            if calls["n"] > 1:        # the registry "disappears" after the turn started
                raise errors.BridgeError(errors.SESSION_CORRUPT, "lost", code="registry_lost")
            return real_get(self, key, ident)

        def bind(self, *a, **k):
            raise errors.BridgeError(errors.SESSION_CORRUPT, "lost", code="registry_lost")

        with mock.patch.object(state.Registry, "get", get), mock.patch.object(state.Registry, "bind_thread", bind):
            answer = svc.execute(CONSULT, "again")
        self.assertIn("PARTIAL SUCCESS", answer)
        self.assertNotIn("already references", answer)

    def test_independent_facts_are_all_reported_when_several_apply(self):
        """persistence + changed repository + in-doubt process state are separate warnings."""
        with mock.patch.object(state.Registry, "bind_thread",
                               mock.Mock(side_effect=errors.BridgeError(errors.STATE_ERROR, "x",
                                                                        code="registry_unwritable"))):
            self.w.set(exec={"mutate": {"path": "changed.txt", "content": "x"}})
            answer = self.service().execute(CONSULT, "q")
        self.assertTrue(answer.startswith("[bridge-warning] 2 warning(s)"), answer[:120])
        self.assertIn("PARTIAL SUCCESS", answer)
        self.assertIn("repository CHANGED", answer)


# =========================================================================== N7
class N7_InfrastructureEvidence(WorldCase):
    def test_pre_completion_error_event_with_a_signature_is_not_ordinary_success(self):
        self.w.set(exec={"events": [thread_started(), {"type": "error", "message": HELPER}, completed()]})
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SANDBOX_UNAVAILABLE, "turn_sandbox_setup_failed"))

    def test_reconnect_noise_without_a_signature_stays_tolerated(self):
        self.w.set(exec={"events": [thread_started(), {"type": "error", "message": "Reconnecting... 2/5"},
                                    completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_signature_deep_inside_a_long_failed_output_is_found(self):
        long_output = ("x" * 20_000) + "\n" + HELPER                       # far beyond any 8 KiB prefix
        self.w.set(exec={"events": [thread_started(), cmd_item(long_output), completed()]})
        self.assertEqual(expect_error(self, self.service()).code, "turn_sandbox_setup_failed")

    def test_oversized_line_with_a_signature_is_counted_not_skipped(self):
        big = ('{"type":"item.completed","item":{"type":"command_execution","status":"failed","aggregated_output":"'
               + "y" * (runner.MAX_LINE_BYTES + 5000) + HELPER + '"}}')
        self.w.set(exec={"events": [thread_started(), big, completed()]})
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "turn_sandbox_setup_failed")
        rec = self.w.telemetry()[0]
        self.assertEqual(rec["events"]["oversize_lines"], 1)
        self.assertEqual(rec["infra"]["oversize_signature_lines"], 1)

    def test_oversized_line_without_a_signature_is_not_a_failure(self):
        big = ('{"type":"item.completed","item":{"type":"command_execution","status":"completed","exit_code":0,'
               '"aggregated_output":"' + "z" * (runner.MAX_LINE_BYTES + 5000) + '"}}')
        self.w.set(exec={"events": [thread_started(), big, completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_repository_read_failures_beyond_not_a_git_repository(self):
        for text in ("fatal: unable to read tree (4b825dc6)", "fatal: bad object HEAD",
                     "fatal: could not open 'src/Main.java' for reading: Permission denied",
                     "error: unable to read sha1 file of a.txt (deadbeef)"):
            self.setUp()                                   # a fresh world per case: build cwd from THIS repo
            in_repo = Path(self.w.repo).as_uri()
            self.w.set(exec={"events": [thread_started(), cmd_item(text, exit_code=128, cwd=in_repo), completed()]})
            self.assertEqual(expect_error(self, self.service()).code, "turn_repo_access_failed", text)

    def test_ordinary_failures_remain_ordinary(self):
        for text in ("1 file changed", "error: pathspec 'nope' did not match any file(s) known to git",
                     "grep: no matches", "Permission denied (publickey)."):
            self.setUp()
            in_repo = Path(self.w.repo).as_uri()
            self.w.set(exec={"events": [thread_started(), cmd_item(text, exit_code=1, cwd=in_repo), completed()]})
            self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER", text)

    def test_repo_bound_signatures_are_ignored_when_the_command_ran_elsewhere(self):
        elsewhere = Path(self.w.tmp / "not_the_repo").as_uri()
        self.w.set(exec={"events": [thread_started(),
                                    cmd_item("fatal: not a git repository", exit_code=128, cwd=elsewhere),
                                    completed()]})
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")


# =========================================================================== N8
class N8_CapabilityDrift(WorldCase):
    def test_allowed_servers_are_reported_from_the_final_readout(self):
        # not enabled at the first readout, enabled by the second: must still be reported
        self.w.set(mcp_sequence=[[], [{"name": "docs", "source": "config"}]])
        answer = self.service(GPT_ARCHITECT_MCP_ALLOW="docs").execute(CONSULT, "q")
        self.assertIn("GPT_ARCHITECT_MCP_ALLOW", answer)
        self.assertEqual(self.w.telemetry()[0]["capabilities"]["mcp_allowed"], ["docs"])
        self.assertIn("mcp_allowed_servers", self.w.telemetry()[0]["capabilities"]["uncontained"])

    def test_server_enabled_between_audit_and_exec_blocks_inference(self):
        quiet = [{"name": "idea", "source": "config"}]
        self.w.set(mcp_sequence=[quiet, quiet, quiet + [{"name": "sneaky", "source": "config"}]])
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "mcp_changed_before_exec"))
        self.assertIn("sneaky", str(exc))
        self.assertEqual(self.w.exec_entries(), [])

    def test_server_enabled_during_the_turn_is_reported(self):
        quiet = [{"name": "idea", "source": "config"}]
        self.w.set(mcp_sequence=[quiet, quiet, quiet, quiet + [{"name": "late", "source": "config"}]])
        answer = self.service().execute(CONSULT, "q")
        self.assertIn("MCP configuration changed while this turn ran", answer)
        self.assertIn("mcp_config_changed_during_call", self.w.telemetry()[0]["warnings"])

    def test_duplicate_feature_rows_fail_closed(self):
        with self.assertRaises(errors.BridgeError) as cm:
            codexcli.parse_features("hooks stable true\nhooks stable false\n")
        self.assertEqual(cm.exception.code, "features_schema_incompatible")
        self.w.set(features_raw="hooks stable true\nhooks stable false")
        self.assertEqual(expect_error(self, self.service()).code, "features_schema_incompatible")

    def test_resolved_evidence_is_labelled_snapshot_scoped(self):
        self.service().execute(CONSULT, "q")
        rc, out, _ = run_admin(self.w.env(), "status", "--json")
        caps = {c["name"]: c for c in json.loads(out)["capabilities"]["capabilities"]}
        self.assertIn("snapshot-scoped", caps["mcp_servers"]["note"])
        self.assertIn("no deny-all", caps["mcp_servers"]["note"])


# =========================================================================== N10
class N10_InstanceIdentity(WorldCase):
    def _established(self):
        self.service().execute(CONSULT, "q")

    def test_corrupt_instance_with_a_deleted_registry_is_not_a_first_initialisation(self):
        self._established()
        (self.w.state / "registry.json").unlink()
        (self.w.state / "instance.json").write_text("{not json")
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_CORRUPT, "instance_invalid"))
        self.assertEqual(len(self.w.exec_entries()), 1)
        self.assertFalse((self.w.state / "registry.json").exists())

    def test_unreadable_instance_file_fails_closed(self):
        self._established()
        (self.w.state / "registry.json").unlink()
        (self.w.state / "instance.json").unlink()
        (self.w.state / "instance.json").mkdir()          # exists but cannot be read as a file
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "instance_unreadable")

    def test_registry_present_but_instance_missing_or_registry_without_identity(self):
        self._established()
        (self.w.state / "instance.json").unlink()
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_MISMATCH, "registry_identity_unverifiable"))
        self.setUp()
        self._established()
        data = json.loads((self.w.state / "registry.json").read_text())
        data.pop("instance_id")
        (self.w.state / "registry.json").write_text(json.dumps(data))
        self.assertEqual(expect_error(self, self.service()).code, "registry_identity_unverifiable")
        self.setUp()
        self._established()
        data = json.loads((self.w.state / "registry.json").read_text())
        data["instance_id"] = 12345                       # non-string
        (self.w.state / "registry.json").write_text(json.dumps(data))
        self.assertEqual(expect_error(self, self.service()).code, "registry_identity_unverifiable")

    def test_instance_with_a_malformed_shape_is_invalid(self):
        self._established()
        for body in ('{"instance_id": "short", "registry_established": true}',
                     '{"instance_id": "' + "a" * 32 + '", "registry_established": "yes"}', "[]"):
            (self.w.state / "instance.json").write_text(body)
            self.assertEqual(expect_error(self, self.service()).code, "instance_invalid", body)

    def test_restore_prev_validates_schema_and_identity_before_replacing(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        svc.execute(CONSULT, "b")                          # now registry.prev.json exists
        reg = state.Registry(self.w.state)
        good = (self.w.state / "registry.prev.json").read_text()
        (self.w.state / "registry.json").unlink()
        for bad in ('{"schema_version": 99, "sessions": {}, "retired": []}',
                    json.dumps({**json.loads(good), "instance_id": "f" * 32}),
                    '{"schema_version": 1, "sessions": [], "retired": []}', "not json"):
            (self.w.state / "registry.prev.json").write_text(bad)
            self.assertFalse(reg.restore_prev(), bad[:40])
            self.assertFalse((self.w.state / "registry.json").exists())
        (self.w.state / "registry.prev.json").write_text(good)
        self.assertTrue(reg.restore_prev())
        self.assertTrue((self.w.state / "registry.json").exists())


# =========================================================================== N11 / N15
class N11N15_FingerprintCompletenessAndVisibility(WorldCase):
    env = property(lambda self: dict(os.environ))

    def test_staged_content_change_with_unchanged_status_and_worktree_is_seen(self):
        f = self.w.repo / "f.txt"
        f.write_text("base\n")
        self.w.git("add", "f.txt")
        self.w.git("-c", "user.email=t@e.i", "-c", "user.name=t", "commit", "-q", "-m", "f")
        f.write_text("A\n")
        self.w.git("add", "f.txt")
        f.write_text("worktree-fixed\n")                   # status is "MM"
        before = fingerprint.repo_fingerprint(self.w.repo, self.env)
        # change ONLY the staged bytes (status stays MM, worktree bytes and HEAD untouched)
        blob = subprocess.run(["git", "-C", str(self.w.repo), "hash-object", "-w", "--stdin"], input=b"B\n",
                              capture_output=True, check=True).stdout.decode().strip()
        self.w.git("update-index", "--cacheinfo", f"100644,{blob},f.txt")
        status = self.w.git("status", "--porcelain")
        self.assertIn("MM f.txt", status)
        after = fingerprint.repo_fingerprint(self.w.repo, self.env)
        self.assertTrue(before["ok"] and after["ok"])
        self.assertNotEqual(before["digest"], after["digest"], "staged-only change must change the fingerprint")

    def test_unborn_repository_tracked_content_is_covered(self):
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="unborn_", dir=self.w.tmp))
        subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
        (d / "a.txt").write_text("v1\n")
        subprocess.run(["git", "-C", str(d), "add", "a.txt"], check=True)
        (d / "a.txt").write_text("v2\n")                   # status "AM"
        one = fingerprint.repo_fingerprint(d, self.env)
        (d / "a.txt").write_text("v3 (same status AM)\n")
        two = fingerprint.repo_fingerprint(d, self.env)
        self.assertEqual((one["ok"], one["head"]), (True, "UNBORN"))
        self.assertNotEqual(one["digest"], two["digest"])

    def test_truncated_status_is_unavailable_never_comparable(self):
        for i in range(30):
            (self.w.repo / f"file_{i}.txt").write_text("x")
        with mock.patch.object(fingerprint, "_STATUS_CAP", 64):
            fp = fingerprint.repo_fingerprint(self.w.repo, self.env)
        self.assertFalse(fp["ok"])
        self.assertEqual(fp["reason"], "status_truncated_or_incomplete")
        cmp = fingerprint.compare(fp, fp)
        self.assertFalse(cmp["comparable"])
        self.assertFalse(cmp["changed"])

    def test_unavailable_fingerprint_is_reported_in_the_answer_not_only_telemetry(self):
        with mock.patch.object(fingerprint, "repo_fingerprint",
                               return_value={"ok": False, "head": None, "digest": None, "reason": "timeout"}):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("Repository consistency could NOT be checked (timeout)", answer)
        self.assertTrue(answer.startswith("[bridge-warning] 1 warning(s)"))
        self.assertIn("repo_fingerprint_unavailable:timeout", self.w.telemetry()[0]["warnings"])


# =========================================================================== N13 / N15
@unittest.skipUnless(WIN, "the source probe targets the Windows sandbox")
class N13N15_SourceProbe(WorldCase):
    def _track(self, *names):
        for n in names:
            p = self.w.repo / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("content\n")
        self.w.git("add", "-A")
        self.w.git("-c", "user.email=t@e.i", "-c", "user.name=t", "commit", "-q", "-m", "more")

    def test_unsafe_names_are_never_selected_and_source_files_are_preferred(self):
        names = ["a&ver", "has space.txt", "-leading.txt", "x%PATH%.txt", "q'uote.txt"]
        try:
            self._track(*names)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("this filesystem cannot create the unsafe file names")
        (self.w.repo / "README.md").unlink()          # leave only unsafe names + one safe source file
        self.w.git("rm", "-q", "--cached", "README.md")
        self._track("src/Main.java")
        probe, why = codexcli._probe_file(self.w.repo, dict(os.environ))
        self.assertEqual((probe, why), ("src/Main.java", None))

    def test_only_unsafe_names_means_a_visible_skip_not_a_pass_through_cmd(self):
        self.w.git("rm", "-q", "-f", "README.md")
        try:
            self._track("a&ver")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("this filesystem cannot create the unsafe file name")
        answer = self.service().execute(CONSULT, "q")
        self.assertIn("source-readability probe was SKIPPED", answer)
        self.assertIn("no_safe_small_tracked_file", answer)
        warnings = self.w.telemetry()[0]["warnings"]
        self.assertTrue(any(w.startswith("sandbox_preflight_source_read_skipped") for w in warnings), warnings)
        cmds = [e["cmd"] for e in self.w.log_entries("sandbox")]
        self.assertFalse(any(c[:2] == ["cmd", "/c"] and "type" in c for c in cmds))

    def test_probe_runs_findstr_with_the_name_as_data_not_cmd_type(self):
        self.service().execute(CONSULT, "q")
        third = self.w.log_entries("sandbox")[2]["cmd"]
        self.assertEqual(third[0], "findstr")
        self.assertNotIn("cmd", third[:1])


# =========================================================================== N14
class N14_IdentifiersAreValidatedAtIngress(WorldCase):
    def test_malformed_thread_id_is_dropped_and_never_echoed_or_persisted(self):
        leak = "C:/Users/kian/private/SecretProject/auth.py"
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": leak}, completed()]})
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.OUTPUT_INCOMPLETE, "bad_thread_id"))
        self.assertNotIn("SecretProject", str(exc))
        blob = self.w.telemetry_text()
        for p in self.w.state.rglob("*"):
            if p.is_file():
                blob += p.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("SecretProject", blob)
        self.assertNotIn(leak, blob)

    def test_telemetry_nested_strings_are_restricted_to_opaque_tokens(self):
        rec = telemetry.sanitise({
            "call_id": "ab" * 16, "tool": CONSULT, "started_at": "2026-10-12T10:00:00Z",
            "session": {"name": "default", "repo_key": "c0ffee" * 5, "thread_id": "C:/Users/x/secret.py",
                        "resumed": True},
            "warnings": ["state_not_saved:registry_unwritable", "C:/Users/kian/proj/a.py", "free text here"],
            "process": {"ownership": "job_object", "note": "has spaces and /slashes/"},
            "route": {"model": "gpt-6.1-sol"}, "usage": {"input_tokens": 3},
            "infra": {"kinds": {"sandbox_setup": 1}},
        })
        self.assertIsNone(rec["session"]["thread_id"])
        self.assertEqual(rec["warnings"], ["state_not_saved:registry_unwritable", "<invalid>", "<invalid>"])
        self.assertEqual(rec["process"], {"ownership": "job_object", "note": "<invalid>"})
        self.assertEqual(rec["infra"], {"kinds": {"sandbox_setup": 1}})
        self.assertEqual(rec["usage"], {"input_tokens": 3})

    def test_a_valid_thread_id_still_flows_everywhere(self):
        self.service().execute(CONSULT, "q")
        rec = self.w.telemetry()[0]
        self.assertTrue(rec["session"]["thread_id"])
        self.assertEqual(rec["session"]["thread_id"],
                         next(iter(self.w.registry()["sessions"].values()))["thread_id"])


# =========================================================================== verification prompt
class VerificationPromptBuilder(unittest.TestCase):
    """The tool that prepares (never sends) the second-round prompt for GPT."""

    def setUp(self):
        import tempfile

        from tools import make_verification_prompt as m

        self.m = m
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _tree(self, skip: str | None = None) -> None:
        for rel in [*self.m.FULL, *self.m.DIFF_ONLY]:
            if rel == skip:
                continue
            p = self.tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {rel}\nsecond line\n", encoding="utf-8")
        old = self.tmp / self.m.SNAPSHOT / self.m.DIFF_ONLY[0]
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("content of an older version\n", encoding="utf-8")

    def test_the_material_the_reviewer_said_was_missing_is_embedded(self):
        for need in ("bridge/redact.py", "bridge/gitsafe.py", "bridge/ids.py", "bridge/execpath.py",
                     "tests/fake_codex.py", "tests/helpers.py",
                     "tools/record_evidence.py", "tools/live_smoke_test.py", "tools/make_v1_diff.py",
                     "README.md", "REVIEW_PACKAGE.md", "docs/remediation4_stat.txt",
                     "tools/prefix_check.py", "tools/smoke_oracles.py", "tests/test_review4.py"):
            self.assertIn(need, self.m.FULL, need)
        # the evidence, the retained raw log, the v1 fixtures and the previous review come by pattern
        self.assertIn("docs/evidence/tests_*.log", self.m.GLOB_LISTED)    # hash-listed, not embedded
        for pattern in ("docs/evidence/source_fingerprint.json", "docs/evidence/prefix_check.log",
                        "docs/evidence/integrity.json", "docs/evidence/cli_observations.json",
                        "tests/fixtures/*.json", "docs/gpt_verification/gpt_verification_*.md"):
            self.assertIn(pattern, self.m.GLOB_FULL, pattern)

    def test_assemble_embeds_files_diffs_and_records_hashes_without_sending(self):
        import hashlib

        self._tree()
        (self.tmp / "docs" / "evidence").mkdir(parents=True, exist_ok=True)
        (self.tmp / "docs" / "evidence" / "tests_final.json").write_text('{"outcome": "OK"}', encoding="utf-8")
        prompt, manifest = self.m.assemble(self.tmp)
        self.assertEqual(manifest["status"], "prepared_not_sent")
        self.assertEqual(manifest["prompt_sha256"], hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        self.assertEqual(manifest["prompt_bytes"], len(prompt.encode("utf-8")))
        by_path = {f["path"]: f for f in manifest["files"]}
        self.assertIn("=== FILE: bridge/redact.py ===", prompt)
        self.assertIn("=== FILE: docs/evidence/tests_final.json ===", prompt)
        expected = hashlib.sha256((self.tmp / "bridge/redact.py").read_bytes()).hexdigest()
        self.assertEqual(by_path["bridge/redact.py"]["sha256"], expected)
        diff_entry = by_path[self.m.DIFF_ONLY[0]]
        self.assertEqual(diff_entry["mode"], "diff_vs_verified_snapshot")
        self.assertIn(f"=== DIFF (vs the version you verified last round): {self.m.DIFF_ONLY[0]} ===", prompt)
        self.assertIn("-content of an older version", prompt)
        for header in ("IMPLEMENTER CLAIMS TO FALSIFY", "LIMITS THE IMPLEMENTER ALREADY DECLARES",
                       "WHAT TO PRODUCE"):
            self.assertIn(header, prompt)
        for finding in [f"R{i}" for i in range(1, 14)]:
            self.assertRegex(prompt, rf"(?m)^{finding}\s", finding)

    def test_a_missing_required_file_is_refused(self):
        self._tree(skip="bridge/redact.py")
        with self.assertRaises(FileNotFoundError):
            self.m.assemble(self.tmp)

    def test_stale_failing_or_missing_evidence_is_refused(self):
        ev = self.tmp / "docs" / "evidence"
        self.assertTrue(any("missing" in p or "no docs/evidence" in p
                            for p in self.m.evidence_problems(self.tmp, "a" * 64)))
        ev.mkdir(parents=True)
        write = lambda name, obj: (ev / name).write_text(json.dumps(obj), encoding="utf-8")   # noqa: E731
        good = "a" * 64
        write("source_fingerprint.json", {"combined_sha256": good})
        write("capability_policy_real_cli.json", {"source_combined_sha256": good})
        import hashlib

        log = b"Ran 5 tests in 1.0s\n\nOK\n"
        (ev / "tests_x.log").write_bytes(log)
        ok = {"source_combined_sha256": good, "source_sha256_after_run": good, "outcome": "OK", "failing": [],
              "tests_run": 5, "exit_code": 0, "skipped": 0, "raw_log": "tests_x.log",
              "raw_log_sha256": hashlib.sha256(log).hexdigest(), "executed_from": "staged_copy",
              "source_sha256_staged_copy": good}
        write("tests_x.json", ok)
        for name in ("integrity.json", "cli_observations.json", "prefix_check.json"):
            write(name, {"source_combined_sha256": good})
        self.assertEqual(self.m.evidence_problems(self.tmp, good), [])
        # stale: bound to some other hash
        self.assertTrue(any("stale" in p for p in self.m.evidence_problems(self.tmp, "b" * 64)))
        # a test record that is not a pass
        write("tests_x.json", {**ok, "outcome": "FAILED", "failing": ["t"]})
        self.assertTrue(any("passing run" in p for p in self.m.evidence_problems(self.tmp, good)))
        write("tests_x.json", {**ok, "tests_run": None})
        self.assertTrue(any("passing run" in p for p in self.m.evidence_problems(self.tmp, good)))

    def test_the_tool_has_no_way_to_send_anything(self):
        """Structural: the module imports nothing that can start a process or open a connection
        (the prompt text may legitimately *mention* run_turn etc.; only imports are checked)."""
        import ast

        tree = ast.parse((ROOT / "tools" / "make_verification_prompt.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & {"subprocess", "socket", "urllib", "http", "requests", "asyncio", "threading",
                                     "bridge", "mcp", "multiprocessing", "ctypes"}, set())
        # tools.record_evidence is imported lazily, only to compute the source hash
        self.assertLessEqual(imported, {"__future__", "difflib", "hashlib", "json", "pathlib", "re", "sys",
                                        "time", "tools"})


if __name__ == "__main__":
    unittest.main()
