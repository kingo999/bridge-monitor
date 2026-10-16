"""Regression tests for the post-round-6 smoke-oracle delta (GPT round-6 findings U1 and the smoke part of U10).

Pure functions and mocked records only: no model, no child process.  Written so the module can be run
against the round-6 source (`.baseline_post_remediation6`): new parameters/functions are reached
defensively, so an unfixed tree yields test FAILURES rather than an import error.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

from bridge import admin, config, errors, runner, state
from tests.helpers import WorldCase
from tools import apply_patch_attack_test as attack
from tools import smoke_oracles as so

CONSULT = config.CONSULT
NAME = "WRITE_ATTEMPT_FRESH.txt"
HEAD = "0123456789abcdef0123456789abcdef01234567"
REPO = r"C:\work\repo"
DENIED = "Set-Content : Access to the path 'C:\\work\\repo\\x' is denied."
PATCH = r"*** Begin Patch\n*** Add File: x.txt\n+x\n*** End Patch"


THREAD = "aaaaaaaa-1111-2222-3333-444444444444"


# The helpers below are deliberately LOCAL: `tools/prefix_check.py` copies only the harness and THIS module
# over the pre-fix source, so importing other test modules would make the check fail at import time.
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


class Base(WorldCase):
    def key(self) -> str:
        _, repo_key = state.canonical_repo(str(self.w.repo))
        return state.session_key(repo_key, "gpt-6.1-sol", "default")

    def registry(self) -> state.Registry:
        reg = state.Registry(self.w.state)
        reg.ensure_dirs()
        return reg

    def stale_marker(self, **overrides) -> state.Registry:
        reg = self.registry()
        marker = {"state": "running", "phase": "running", "call_id": "c" * 8, "owner_pid": 2_000_000_000,
                  "owner_identity": "1", "thread_id": None, "ownership": "job_object", "resumed": False,
                  "child_pid": 1234, "model": "gpt-6.1-sol"}
        marker.update(overrides)
        self.assertTrue(reg.write_marker("inflight", self.key(), marker))
        return reg


def native(command, output, **extra):
    args = {"command": command, **extra}
    return [{"payload": {"type": "function_call", "call_id": "n0", "name": "shell", "arguments": json.dumps(args)}},
            {"payload": {"type": "function_call_output", "call_id": "n0", "output": output}}]


def js(body, output):
    return [{"payload": {"type": "custom_tool_call", "call_id": "j0", "name": "exec", "input": body}},
            {"payload": {"type": "custom_tool_call_output", "call_id": "j0", "output": output}}]


def steps(rows, repo=None):
    records = so.command_records(rows)
    return so.probe_steps(records, NAME, repo=repo) if repo is not None else so.probe_steps(records, NAME)


class U1_PositionalRolesAndTargetBinding(unittest.TestCase):
    def test_a_positional_VALUE_equal_to_the_probe_name_is_not_an_attempt_to_write_it(self):
        for command in (f"Set-Content other.txt {NAME}", f"Add-Content other.txt {NAME}",
                        f"Set-Content -Path other.txt {NAME}", f"Set-Content -Value {NAME} other.txt"):
            self.assertFalse(steps(native(command, DENIED))["create_file"]["attempted"], command)

    def test_the_target_in_path_position_is_still_an_attempt(self):
        for command in (f"Set-Content {NAME} WRITE-PROBE", f"Set-Content -Path .\\{NAME} -Value WRITE-PROBE",
                        f"Set-Content -LiteralPath {NAME} -Value WRITE-PROBE", f"New-Item {NAME}",
                        f"Out-File {NAME}", f"touch {NAME}", f"echo WRITE-PROBE > {NAME}"):
            self.assertTrue(steps(native(command, DENIED))["create_file"]["attempted"], command)

    def test_an_unrecognised_or_colon_flag_or_whatif_makes_the_command_unattributable(self):
        for command in (f"Set-Content -WhatIf {NAME} x", f"Set-Content -Path:other.txt {NAME}",
                        f"Set-Content {NAME} x -Wibble", f"Set-Content -Confirm:$false {NAME} x"):
            self.assertFalse(steps(native(command, DENIED))["create_file"]["attempted"], command)

    def test_cmd_expansion_and_escape_syntax_is_not_data(self):
        for command in (f"echo %OTHER% > {NAME}", f"echo !OTHER! > {NAME}", f"echo ^& > {NAME}",
                        f"Set-Content {NAME} %OTHER%"):
            self.assertIsNone(so.tokenize(command), command)
            self.assertFalse(steps(native(command, DENIED))["create_file"]["attempted"], command)

    def test_doubled_quotes_are_not_silently_concatenated(self):
        for text in ('echo "a""b"', "echo 'a''b'"):
            self.assertIsNone(so.tokenize(text), text)

    def test_a_commit_must_be_the_plain_command_in_the_disposable_repository(self):
        for command in ("git -C other commit --allow-empty -m probe", "git -c user.name=x commit -m probe",
                        "git --git-dir=other commit -m probe", "git --work-tree=other commit -m probe",
                        "git --no-pager -C other commit -m probe"):
            self.assertFalse(steps(native(command, "fatal: x"))["git_commit"]["attempted"], command)
        for command in ("git commit --allow-empty -m probe", "git.exe commit --allow-empty -m probe"):
            self.assertTrue(steps(native(command, "fatal: x"))["git_commit"]["attempted"], command)

    def test_a_call_run_in_another_working_directory_is_not_attributed_to_the_repository(self):
        command = f"Set-Content {NAME} WRITE-PROBE"
        other = steps(native(command, DENIED, workdir=r"C:\somewhere\else"), repo=REPO)
        self.assertFalse(other["create_file"]["attempted"])
        unknown = steps(native(command, DENIED, workdir=r"C:\somewhere\else"))
        self.assertFalse(unknown["create_file"]["attempted"], "without the repo path a workdir is unattributable")
        same = steps(native(command, DENIED, workdir=REPO + "\\"), repo=REPO)
        self.assertTrue(same["create_file"]["attempted"])
        default = steps(native(command, DENIED), repo=REPO)
        self.assertTrue(default["create_file"]["attempted"], "no workdir means the session's own directory")

    def test_the_control_is_not_attributed_when_it_ran_elsewhere(self):
        rows = native("git rev-parse HEAD", HEAD, workdir=r"C:\somewhere\else")
        records = so.command_records(rows)
        self.assertFalse(so.control_executed(records, HEAD, repo=REPO))
        ok = so.command_records(native("git rev-parse HEAD", HEAD))
        self.assertTrue(so.control_executed(ok, HEAD, repo=REPO))

    def test_a_js_passthrough_with_a_foreign_workdir_is_not_attributed(self):
        body = f'text(await tools.exec_command({{cmd: "Set-Content {NAME} x", workdir: "C:\\\\other"}}));'
        self.assertFalse(steps(js(body, DENIED), repo=REPO)["create_file"]["attempted"])
        plain = f'text(await tools.exec_command({{cmd: "Set-Content {NAME} x"}}));'
        self.assertTrue(steps(js(plain, DENIED), repo=REPO)["create_file"]["attempted"])

    def test_the_outer_attack_wrapper_must_be_apply_patch_or_exec(self):
        body = f'text(await tools.apply_patch("{PATCH}"));'
        self.assertFalse(attack.wrapper_analysis("mystery_tool", body)["authentic"])
        self.assertTrue(attack.wrapper_analysis("exec", body)["authentic"])
        self.assertTrue(attack.wrapper_analysis("apply_patch", "*** Begin Patch\n*** End Patch")["authentic"])


class U10_EnforcementIsSeparateFromInventory(unittest.TestCase):
    def overall(self, results, **kw):
        return so.overall(results, **kw)

    def test_all_enforcement_checks_conclusive_is_conclusive_despite_an_unassessed_inventory(self):
        code, lines = self.overall([("a", "PASS", ""), ("b", "PASS", "")])
        self.assertEqual(code, 0)
        self.assertTrue(any("inventory" in l.lower() and "not assessed" in l.lower() for l in lines), lines)

    def test_an_inconclusive_check_is_named_and_explained_by_its_own_reason(self):
        code, lines = self.overall([("a", "PASS", ""), ("create step", "INCONCLUSIVE", "not attempted")])
        self.assertEqual(code, 3)
        text = "\n".join(lines)
        self.assertIn("create step", text)
        self.assertNotIn("write-block was not actually exercised", text,
                         "that sentence is only true for the enforcement steps, and is false for other checks")

    def test_a_failure_wins_and_is_reported_as_an_observed_violation_not_missing_evidence(self):
        code, lines = self.overall([("a", "FAIL", "the filesystem changed"), ("b", "INCONCLUSIVE", "")])
        self.assertEqual(code, 1)
        self.assertTrue(any("FAIL" in l for l in lines), lines)


class U11_ObservedShapesAndTheProbeItself(unittest.TestCase):
    """What the first live run showed: numeric option literals in the exec wrapper, and a probe the model
    declined to act on.  The probe's own commands must be attributable by the oracle."""

    def control(self, args):
        body = f"text(await tools.exec_command({args}));"
        return so.control_executed(so.command_records(js(body, HEAD)), HEAD, repo=REPO)

    def test_integer_and_boolean_option_literals_of_the_closed_list_are_attributed(self):
        self.assertTrue(self.control('{cmd:"git rev-parse HEAD",max_output_tokens:1000}'))
        self.assertTrue(self.control('{cmd: "git rev-parse HEAD", yield_time_ms: 500, tty: false}'))

    def test_any_other_non_string_value_makes_the_call_unattributable(self):
        for args in ('{cmd:"git rev-parse HEAD",foo:1}', '{cmd:"git rev-parse HEAD",with_escalated_permissions:true}',
                     '{cmd:"git rev-parse HEAD",max_output_tokens:1e3}', '{cmd:"git rev-parse HEAD",max_output_tokens:-1}',
                     '{cmd:"git rev-parse HEAD",max_output_tokens:limit}', '{cmd: 5}',
                     '{cmd:"git rev-parse HEAD",max_output_tokens:1000 + 1}'):
            self.assertFalse(self.control(args), args)

    def probe_commands(self):
        import re
        from tools import live_smoke_test as smoke

        text = smoke.PROBE.format(name=NAME)
        return text, re.findall(r"`([^`]+)`", text)

    def test_the_probe_asks_for_attempts_one_command_per_call(self):
        text, _ = self.probe_commands()
        low = text.lower()
        for needle in ("attempting each one is the test", "do not skip it", "its own separate tool call",
                       "only that one command", "operator's authority"):
            self.assertIn(needle, low)

    def test_every_command_the_probe_requests_is_attributable_by_the_oracle(self):
        _, commands = self.probe_commands()
        self.assertIn("git rev-parse HEAD", commands)
        rows = []
        for k, command in enumerate(c for c in commands if c != "git rev-parse HEAD"):
            rows += [{"payload": {"type": "function_call", "call_id": f"p{k}", "name": "shell",
                                  "arguments": json.dumps({"command": command})}},
                     {"payload": {"type": "function_call_output", "call_id": f"p{k}", "output": DENIED}}]
        got = steps(rows, repo=REPO)
        for step in ("create_file", "append_canary", "git_commit"):
            self.assertTrue(got[step]["attempted"], f"the probe's command for {step} is not attributable")
            self.assertTrue(got[step]["refusal_recorded"], step)
        self.assertTrue(self.control('{cmd:"git rev-parse HEAD",max_output_tokens:1000}'))

    def test_the_probe_names_the_target_it_will_be_judged_on(self):
        text, commands = self.probe_commands()
        self.assertTrue(any(NAME in c for c in commands) and NAME + ".patch.txt" in text)


class U12_WriteRefusalsAreNotInfrastructureFailures(WorldCase):
    """A read-only sandbox refusing a WRITE is the control working, not a broken repository.  Observed live:
    `git commit` -> `fatal: Unable to create '.../.git/index.lock': Permission denied` withheld the whole
    answer as `turn_repo_access_failed`."""

    RECORDED = ("fatal: Unable to create 'C:/AI/gpt-claude-mcp-bridge-v2/.smoke_work/smoke_tljw_kfa/repo/"
                ".git/index.lock': Permission denied\n")
    WRITE_REFUSALS = (
        RECORDED,
        "fatal: could not create work tree dir 'x': Permission denied",
        "fatal: cannot create directory at 'x': Permission denied",
        "fatal: could not open '.git/FETCH_HEAD' for writing: Permission denied",
        "fatal: unable to write new index file: Permission denied",
        "fatal: Unable to lock '.git/refs/heads/x.lock': Permission denied",
    )
    READ_FAILURES = (
        "fatal: could not open 'src/Main.java' for reading: Permission denied",
        "fatal: cannot open '.git/HEAD': Permission denied",
        "fatal: not a git repository (or any of the parent directories): .git",
        "fatal: unable to read tree (4b825dc6)",
    )

    def test_write_and_lock_refusals_are_not_repository_access_failures(self):
        for text in self.WRITE_REFUSALS:
            self.assertEqual(runner.signature_kinds(text, in_repo=True), [], text)

    def test_read_failures_and_unreadable_repositories_are_still_flagged(self):
        for text in self.READ_FAILURES:
            self.assertEqual(runner.signature_kinds(text, in_repo=True), ["repo_access"], text)

    def test_a_write_refusal_does_not_hide_a_read_failure_on_another_line(self):
        for read in self.READ_FAILURES:
            text = self.RECORDED + "\n" + read
            self.assertEqual(runner.signature_kinds(text, in_repo=True), ["repo_access"], read)

    def test_a_sandbox_refused_git_commit_does_not_withhold_the_answer(self):
        in_repo = Path(self.w.repo).as_uri()
        self.w.set(exec={"events": [thread_started(), cmd_item(self.RECORDED, exit_code=128, cwd=in_repo),
                                    completed()]})
        self.assertEqual(self.service().execute(config.CONSULT, "q"), "FAKE ANSWER")

    def test_a_real_repository_read_failure_still_withholds_the_answer(self):
        in_repo = Path(self.w.repo).as_uri()
        text = "fatal: could not open 'src/Main.java' for reading: Permission denied"
        self.w.set(exec={"events": [thread_started(), cmd_item(text, exit_code=128, cwd=in_repo), completed()]})
        with self.assertRaises(errors.BridgeError) as cm:
            self.service().execute(config.CONSULT, "q")
        self.assertEqual(cm.exception.code, "turn_repo_access_failed")


class U3_MarkerFailuresAfterCommitAreReportedNotRaised(Base):
    def _unwritable_bind(self):
        return mock.patch.object(state.Registry, "bind_thread",
                                 mock.Mock(side_effect=errors.BridgeError(errors.STATE_ERROR, "x",
                                                                          code="registry_unwritable")))

    def _failing_clear(self, *kinds):
        real = state.Registry.clear_marker

        def clear(self_, kind, key):
            if kind in kinds:
                raise OSError("the marker lock could not be taken")
            return real(self_, kind, key)

        return mock.patch.object(state.Registry, "clear_marker", clear)

    def _pending(self, reg):
        self.assertTrue(reg.write_marker("pending", self.key(), {
            "thread_id": THREAD, "call_id": "c" * 8, "reason": "registry_unwritable",
            "since": state.utc_now(), "model": "gpt-6.1-sol"}))

    # ---- the service path
    def test_a_failed_final_retained_write_is_reported(self):
        real = state.Registry.write_marker

        def fail(self_, kind, key, payload):
            if kind == "pending" or payload.get("persistence_reason"):
                return False
            return real(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "write_marker", fail), self._unwritable_bind():
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("inflight_marker_unwritable", self.w.telemetry()[0]["warnings"])
        self.assertIn("could NOT be updated", answer)

    def test_a_marker_clear_failure_after_the_pointer_committed_still_returns_the_answer(self):
        with self._failing_clear("inflight"):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("FAKE ANSWER", answer)
        record = self.w.telemetry()[0]
        self.assertIn("inflight_marker_not_cleared", record["warnings"])
        self.assertIn("could not be removed", answer)
        entry = state.Registry(self.w.state).snapshot()["sessions"].get(self.key())
        self.assertTrue(entry and entry.get("thread_id"), "the pointer was committed")
        self.assertEqual(record["outcome"], "success_partial_persistence")

    # ---- the administrative commands
    def test_reset_reports_an_undiscardable_pending_marker_instead_of_a_traceback(self):
        reg = self.registry()
        self._pending(reg)
        with self._failing_clear("pending"):
            rc, out, err = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 1, (out, err))
        self.assertIn("COULD NOT discard the pending marker", out)
        self.assertIsNotNone(reg.read_marker("pending", self.key()), "it genuinely remains")

    def test_adopt_reports_an_unclearable_pending_marker_after_the_pointer_is_adopted(self):
        reg = self.registry()
        self._pending(reg)
        with self._failing_clear("pending"):
            rc, out, err = run_admin(self.w.env(), "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 1, (out, err))
        self.assertIn("adopted", out)
        self.assertIn("COULD NOT clear the pending marker", out)
        entry = state.Registry(self.w.state).snapshot()["sessions"].get(self.key())
        self.assertEqual(entry and entry.get("thread_id"), THREAD)

    def test_reconcile_reports_an_unremovable_inflight_marker(self):
        reg = self.stale_marker(state="uncertain", reason="bridge_terminated")
        with self._failing_clear("inflight"):
            rc, out, err = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 1, (out, err))
        self.assertIn("NOT reconciled", out)
        self.assertIsNotNone(reg.read_marker("inflight", self.key()))

    def test_an_unexpected_state_file_failure_is_never_a_bare_traceback(self):
        from bridge import admin

        def boom(*a, **k):
            raise OSError("disk went away")

        with mock.patch.object(admin, "cmd_status", boom):
            rc, out, err = run_admin(self.w.env(), "status")
        self.assertEqual(rc, 1)
        self.assertIn("MAY already have completed", err)


class U4_ReinitLeavesAnIndependentWitness(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sd = Path(self._tmp.name) / "state"
        self.reg = state.Registry(self.sd)
        self.reg.ensure_dirs()

    def test_with_the_journal_failing_a_later_loss_of_registry_and_identity_is_still_loss(self):
        with mock.patch.object(state.Journal, "append", lambda self_, event, **fields: False):
            self.reg.reinit()
        (self.sd / "registry.json").unlink()
        (self.sd / "instance.json").unlink()
        with self.assertRaises(errors.BridgeError) as cm:
            state.Registry(self.sd).snapshot()
        self.assertEqual(cm.exception.code, "registry_lost")

    def test_a_reinit_that_cannot_leave_its_witness_creates_no_registry(self):
        real = state.atomic_write_text

        def fail_prev(path, text, *args, **kwargs):
            if Path(path).name == "registry.prev.json":
                raise OSError("disk full")
            return real(path, text, *args, **kwargs)

        with mock.patch.object(state, "atomic_write_text", fail_prev):
            with self.assertRaises(errors.BridgeError) as cm:
                self.reg.reinit()
        self.assertEqual(cm.exception.code, "registry_unwritable")
        self.assertFalse((self.sd / "registry.json").exists())

    def test_reinit_journals_the_event_itself_not_only_the_admin_command(self):
        self.reg.reinit()
        self.assertTrue(any(r.get("event") == "registry_reinitialised" for r in self.reg.journal.read()))


class U8_AnObservedThreadSurvivesARunnerException(Base):
    def test_a_runner_failure_before_the_observer_ran_still_records_the_thread_debt(self):
        from bridge import jobobject, service

        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real_run = service._CallFence.run
        gated = {"v": False}

        def gate(self_, fn):
            if threading.current_thread().name == "codex-read_stdout" and not gated["v"]:
                gated["v"] = True
                entered.set()
                release.wait(30)          # the parser has set Events.thread_id; the observer is not running yet
            return real_run(self_, fn)

        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": THREAD}, {"type": "turn.completed"}]})
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(service._CallFence, "run", gate))
            stack.enter_context(mock.patch.object(jobobject.ProcessOwnership, "active_processes",
                                                  mock.Mock(side_effect=RuntimeError("supervision failed"))))
            stack.enter_context(mock.patch.object(runner, "READER_JOIN_TIMEOUT_S", 0.5, create=True))
            exc = expect_error(self, self.service())
            self.assertTrue(entered.wait(5), "the reader must have reached the observer")
            self.assertEqual(exc.code, "runner_failed")
            marker = state.Registry(self.w.state).read_marker("inflight", self.key())
            release.set()
            for t in list(threading.enumerate()):
                if t.name == "codex-read_stdout":
                    t.join(10)
        self.assertIsNotNone(marker, "the call's record must remain")
        self.assertEqual(marker.get("unbound_thread"), THREAD, marker)
        self.assertEqual(state.thread_debt(marker, None), THREAD)

    def test_the_exception_carries_the_frozen_observed_identity(self):
        self.assertTrue(hasattr(errors.BridgeError, "observed_thread_id"))
        self.assertIsNone(errors.BridgeError(errors.INTERNAL, "x").observed_thread_id)


if __name__ == "__main__":
    unittest.main()
