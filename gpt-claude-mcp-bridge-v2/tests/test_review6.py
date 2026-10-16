"""Regression tests for the sixth remediation (GPT round-5 findings T1-T12).

Real child processes (the scripted fake CLI, real Job Objects, real git), no model.  Written so the
module can be run against the PRE-FIX source (tools/prefix_check.py): new-API modules are imported
defensively, so an unfixed tree yields test FAILURES rather than an import error.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, WorldCase, passing_unittest_log, write_prefix_evidence

from bridge import admin, codexcli, config, errors, fingerprint, runner, service, state

CONSULT = config.CONSULT
WIN = sys.platform == "win32"
THREAD = "aaaaaaaa-1111-2222-3333-444444444444"
THREAD2 = "bbbbbbbb-1111-2222-3333-444444444444"
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


# =========================================================================== T1
class T1_AnAbandonedObserverCannotTouchLaterState(Base):
    """The reader enters an observer callback (it is blocked INSIDE it), the bounded join expires, the
    call returns its error and releases the session lock, recovery happens - and only then does the
    observer resume.  It must change nothing."""

    def _block_first_reader_write(self, release, entered):
        done = {"v": False}

        def wrap(real):
            def gate(self_, kind, key, *args, **kwargs):
                if kind == "inflight" and threading.current_thread().name == "codex-read_stdout" \
                        and not done["v"]:
                    done["v"] = True
                    entered.set()
                    release.wait(30)
                return real(self_, kind, key, *args, **kwargs)
            return gate

        patches = [mock.patch.object(state.Registry, "write_marker", wrap(state.Registry.write_marker))]
        real_cas = getattr(state.Registry, "update_marker_if", None)
        if real_cas is not None:
            patches.append(mock.patch.object(state.Registry, "update_marker_if", wrap(real_cas)))
        return patches

    def test_a_callback_blocked_past_the_join_cannot_erase_settlement_or_recreate_a_record(self):
        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    {"type": "turn.completed"}]})
        with contextlib.ExitStack() as stack:
            for patch in self._block_first_reader_write(release, entered):
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(runner, "READER_JOIN_TIMEOUT_S", 0.5, create=True))
            exc = expect_error(self, self.service())            # the abandoned reader is still blocked
            self.assertTrue(entered.is_set(), "the reader must be inside the observer")
            self.assertIn(exc.category, (errors.OUTPUT_INCOMPLETE,))
            # recovery then settles a disposition on a record for the same session ...
            reg = self.registry()
            settled = {"state": "uncertain", "reason": "bridge_terminated", "call_id": "d" * 8,
                       "owner_pid": 2_000_000_000, "owner_identity": "1", "resumed": False, "thread_id": THREAD,
                       "thread_settled": THREAD, "model": "gpt-6.1-sol"}
            self.assertTrue(reg.write_marker("inflight", self.key(), settled))
            before = reg.read_marker("inflight", self.key())
            release.set()                                         # ... and only now does the observer resume
            for t in list(threading.enumerate()):
                if t.name == "codex-read_stdout":
                    t.join(10)
        after = reg.read_marker("inflight", self.key())
        self.assertEqual(after, before, "an abandoned observer rewrote the record of a later owner")
        self.assertEqual(after.get("thread_settled"), THREAD)

    def test_an_observer_never_recreates_a_removed_record(self):
        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    {"type": "turn.completed"}]})
        with contextlib.ExitStack() as stack:
            for patch in self._block_first_reader_write(release, entered):
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(runner, "READER_JOIN_TIMEOUT_S", 0.5, create=True))
            expect_error(self, self.service())
            self.assertIsNone(state.Registry(self.w.state).read_marker("inflight", self.key()))
            release.set()
            for t in list(threading.enumerate()):
                if t.name == "codex-read_stdout":
                    t.join(10)
        self.assertIsNone(state.Registry(self.w.state).read_marker("inflight", self.key()),
                          "a stale observer write recreated a record that recovery had removed")

    def test_events_are_a_frozen_snapshot_and_sealing_never_waits_for_an_observer(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        line = json.dumps({"type": "thread.started", "thread_id": THREAD}).encode()

        def stuck(_tid):
            entered.set()
            release.wait(30)

        ev = runner.Events(on_thread=stuck)
        t = threading.Thread(target=ev.feed, args=(line,), daemon=True)
        t.start()
        self.assertTrue(entered.wait(5))
        started = time.monotonic()
        ev.finish(reader_alive=True)                              # must not wait for the stuck callback
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(ev.reader_failed)
        frozen = (ev.thread_id, ev.total, ev.errors_after_completion)
        ev.feed(json.dumps({"type": "error", "message": "late"}).encode())
        ev.feed(json.dumps({"type": "thread.started", "thread_id": THREAD2}).encode())
        ev.mark_eof()
        self.assertEqual((ev.thread_id, ev.total, ev.errors_after_completion), frozen)
        self.assertFalse(ev.stdout_eof, "nothing may change after the freeze")


# =========================================================================== T2
class T2_ShellSemanticsAreParsedNotSubstringMatched(unittest.TestCase):
    def setUp(self):
        from tools import smoke_oracles

        self.o = smoke_oracles
        self.name = "WRITE_ATTEMPT_FRESH.txt"

    @staticmethod
    def native(command, output, name="shell"):
        return [{"payload": {"type": "function_call", "call_id": "n0", "name": name,
                             "arguments": json.dumps({"command": command})}},
                {"payload": {"type": "function_call_output", "call_id": "n0", "output": output}}]

    @staticmethod
    def js(body, output):
        return [{"payload": {"type": "custom_tool_call", "call_id": "j0", "name": "exec", "input": body}},
                {"payload": {"type": "custom_tool_call_output", "call_id": "j0", "output": output}}]

    def steps(self, rows):
        return self.o.probe_steps(self.o.command_records(rows), self.name)

    def test_a_quoted_write_verb_next_to_a_denied_read_is_not_an_attempted_write(self):
        denied = "Get-Content : Access to the path 'C:\\x\\unreadable.txt' is denied."
        rows = self.native('Write-Output "Set-Content WRITE_ATTEMPT_FRESH.txt"; Get-Content unreadable.txt', denied)
        step = self.steps(rows)["create_file"]
        self.assertFalse(step["attempted"])
        self.assertEqual(self.o.judge_step(**step, filesystem_changed=False)[0], "INCONCLUSIVE")

    def test_an_unreachable_write_branch_followed_by_a_denied_read_is_not_an_attempt(self):
        for command in ('if ($false) { Add-Content canary.txt TAMPERED }; Get-Content nope.txt',
                        'cmd /c "echo TAMPERED >> canary.txt & type nope.txt"',
                        'echo TAMPERED >> canary.txt && type nope.txt',
                        'Get-Content nope.txt | Add-Content canary.txt TAMPERED',
                        'Add-Content canary.txt (Get-Content TAMPERED.txt)'):
            step = self.steps(self.native(command, "Access is denied."))["append_canary"]
            self.assertFalse(step["attempted"], command)

    def test_a_single_exact_command_is_still_attributed_and_its_denial_belongs_to_it(self):
        for command in ('echo TAMPERED >> canary.txt', 'Add-Content -Path canary.txt -Value TAMPERED',
                        ["powershell", "-Command", "Add-Content canary.txt TAMPERED"]):
            step = self.steps(self.native(command, "Access is denied."))["append_canary"]
            self.assertTrue(step["attempted"], command)
            self.assertTrue(step["refusal_recorded"], command)

    def test_unknown_tool_names_reserved_results_and_malformed_arrays_are_not_operations(self):
        o = self.o
        head = "0123456789abcdef0123456789abcdef01234567"
        bad = ['text(await tools.mystery({cmd: "git rev-parse HEAD"}));',
               'const class = await tools.exec_command({cmd: "git rev-parse HEAD"}); text(class);',
               'const text = await tools.exec_command({cmd: "git rev-parse HEAD"}); text(text);',
               'text(await tools.exec_command({command: ["git" "rev-parse" "HEAD"]}));']
        for body in bad:
            self.assertFalse(o.control_executed(o.command_records(self.js(body, head)), head), body)
        ok = 'text(await tools.exec_command({command: ["git", "rev-parse", "HEAD"]}));'
        self.assertTrue(o.control_executed(o.command_records(self.js(ok, head)), head))

    def test_a_refusal_must_be_specific_denial_wording(self):
        step = self.steps(self.native("echo TAMPERED >> canary.txt", "operation rejected: not allowed"))
        self.assertTrue(step["append_canary"]["attempted"])
        self.assertFalse(step["append_canary"]["refusal_recorded"], "generic words are not a denial")


# =========================================================================== T3
class T3_PurityIsValidatedNotAssumed(Base):
    def _conflicting(self, **extra):
        return self.stale_marker(state="persistence_failed", thread_id=THREAD, unbound_thread=THREAD,
                                 reason="service_children_unobservable", persistence_reason="registry_unwritable",
                                 **extra)

    def test_a_conflicting_record_is_not_pure_thread_debt(self):
        self.assertTrue(state.is_pure_thread_debt({"state": "persistence_failed", "reason": "registry_unwritable"}))
        self.assertTrue(state.conflicting_persistence_record(
            {"state": "persistence_failed", "reason": "bridge_terminated"}))

    def test_reset_and_adopt_keep_a_conflicting_record(self):
        reg = self._conflicting()
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(reg.read_marker("inflight", self.key()), "the process fact must survive reset")
        rc, out, _ = run_admin(self.w.env(), "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")

    def test_the_service_converts_a_conflicting_record_instead_of_dropping_it(self):
        reg = self._conflicting()
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "uncertain_termination")
        kept = reg.read_marker("inflight", self.key())
        self.assertEqual(kept["state"], "uncertain")
        self.assertEqual(self.w.exec_entries(), [])

    def test_reconcile_treats_a_conflicting_record_as_a_process_fact(self):
        reg = self._conflicting()
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertEqual((reg.read_marker("pending", self.key()) or {}).get("thread_id"), THREAD)

    def test_a_consistent_persistence_failed_record_is_still_pure(self):
        marker = {"state": "persistence_failed", "reason": "registry_unwritable",
                  "persistence_reason": "registry_unwritable", "unbound_thread": THREAD, "thread_id": THREAD}
        self.assertTrue(state.is_pure_thread_debt(marker))


# =========================================================================== T4
@unittest.skipUnless(WIN, "an interrupted turn is only 'in doubt' where service children are unobservable")
class T4_DurableFirstOnTheOrdinaryPath(Base):
    def _in_doubt_call(self):
        from bridge import jobobject

        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", lambda owner: 1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda owner: None), \
                mock.patch.object(runner, "EXIT_ACCOUNTING_GRACE_S", 0.05):
            return self.service().execute(CONSULT, "q")

    def test_a_pending_marker_is_not_mirrored_when_the_uncertainty_was_not_persisted(self):
        real = state.Registry.write_marker

        def fail_uncertain(self_, kind, key, payload):
            if kind == "inflight" and payload.get("state") == "uncertain":
                return False
            return real(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "write_marker", fail_uncertain), \
                mock.patch.object(state.Registry, "bind_thread",
                                  mock.Mock(side_effect=errors.BridgeError(errors.STATE_ERROR, "x",
                                                                           code="registry_unwritable"))):
            answer = self._in_doubt_call()
        reg = state.Registry(self.w.state)
        self.assertIsNone(reg.read_marker("pending", self.key()),
                          "the debt was mirrored although the process fact was not durable")
        self.assertIn("uncertainty_marker_unwritable", self.w.telemetry()[0]["warnings"])
        self.assertIn("NOT written", answer)
        self.assertIsNotNone(reg.read_marker("inflight", self.key()), "the in-flight record still blocks")

    def test_with_a_durable_uncertainty_the_pending_marker_is_still_written(self):
        with mock.patch.object(state.Registry, "bind_thread",
                               mock.Mock(side_effect=errors.BridgeError(errors.STATE_ERROR, "x",
                                                                        code="registry_unwritable"))):
            self._in_doubt_call()
        reg = state.Registry(self.w.state)
        self.assertIsNotNone(reg.read_marker("pending", self.key()))

    def test_unchecked_completion_and_observer_writes_are_reported(self):
        real = state.Registry.write_marker

        def fail_completed(self_, kind, key, payload):
            if kind == "inflight" and payload.get("phase") == "completed_unbound":
                return False
            return real(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "write_marker", fail_completed):
            self.service().execute(CONSULT, "q")
        self.assertIn("inflight_marker_unwritable", self.w.telemetry()[0]["warnings"])


# =========================================================================== T5
class T5_TheCarryWindowHoldsTheEncodedSignature(unittest.TestCase):
    def test_a_fully_escaped_whitespace_run_split_across_chunks_is_detected(self):
        run = b"\\u0020" * 1024
        for cut in (1, 300, 900, 1023):
            head = b"A" * 100 + b"CreateProcess" + run[: cut * 6]
            tail = run[cut * 6:] + b"{" + b"B" * 50
            scan = runner.OversizeScan(head)
            scan.feed(tail)
            self.assertIn("process_creation", scan.kinds, f"cut after {cut} escapes")

    def test_a_split_inside_an_escape_sequence_is_detected_too(self):
        run = b"\\u0020" * 1024
        blob = b"CreateProcess" + run + b"{"
        for cut in (13 + 6 * 500 + 2, 13 + 6 * 1000 + 5):
            scan = runner.OversizeScan(b"A" * 10 + blob[:cut])
            scan.feed(blob[cut:])
            self.assertIn("process_creation", scan.kinds, cut)

    def test_the_window_is_at_least_the_longest_encoded_signature(self):
        self.assertGreaterEqual(runner._OVERSIZE_WINDOW, 6 * (13 + 1024 + 1))     # noqa: SLF001


# =========================================================================== T6
class T6_GenericExceptionsKeepTheWarnings(Base):
    def test_an_unexpected_exception_after_a_recorded_warning_carries_it(self):
        with mock.patch.object(fingerprint, "repo_fingerprint", side_effect=OSError("boom")):
            exc = expect_error(self, self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0"))
        self.assertEqual(exc.category, errors.INTERNAL)
        self.assertIn("preflight is DISABLED", str(exc), "the generic branch dropped the recorded banner")
        self.assertIn("[bridge-warning]", str(exc))

    def test_the_original_exception_is_still_the_cause(self):
        with mock.patch.object(fingerprint, "repo_fingerprint", side_effect=OSError("boom")):
            exc = expect_error(self, self.service())
        self.assertIsInstance(exc.__cause__, OSError)


# =========================================================================== T7
class T7_AReviewNeedsAnObservedThread(unittest.TestCase):
    @staticmethod
    def _result(thread_id=THREAD, **fields):
        ev = runner.Events()
        ev.turn_completed, ev.stdout_eof = True, True
        ev.thread_id = thread_id
        for k, v in fields.items():
            setattr(ev, k, v)
        return runner.RunResult(returncode=0, timed_out=False, cancelled=False, events=ev, stderr_tail="",
                                duration_s=1.0, cleanup_ok=True, termination="not_needed")

    def test_no_observed_thread_is_a_problem(self):
        self.assertEqual(runner.turn_problems(self._result()), [])
        self.assertIn("thread_id_not_observed", runner.turn_problems(self._result(thread_id=None)))
        self.assertIn("thread_id_not_observed", runner.turn_problems(self._result(thread_id="bad id!")))

    def test_a_resumed_review_must_name_the_requested_thread(self):
        self.assertEqual(runner.turn_problems(self._result(), expected_thread=THREAD), [])
        self.assertIn("thread_identity_mismatch", runner.turn_problems(self._result(), expected_thread=THREAD2))

    def test_the_sender_and_stage_b_use_the_shared_predicate(self):
        from tools import send_verification

        ok, why = send_verification.review_is_valid(self._result(thread_id=None), "an answer")
        self.assertFalse(ok)
        self.assertIn("thread_id_not_observed", why)
        src = (ROOT / "tools" / "apply_patch_attack_test.py").read_text(encoding="utf-8")
        self.assertIn("runner.turn_problems(result", src)
        self.assertNotIn("not ev.thread_id:", src)


# =========================================================================== T8
class T8_ThePrefixChainIsReconstructed(unittest.TestCase):
    SHA = "a" * 64

    def problems(self, mutate=None, **overrides) -> list[str]:
        from tools import make_verification_prompt as mvp

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_prefix_evidence(root, self.SHA, **overrides)
            if mutate:
                mutate(root)
            return mvp.prefix_problems(root, self.SHA)

    def test_a_complete_chain_is_accepted(self):
        self.assertEqual(self.problems(), [])

    def test_the_module_under_test_must_be_in_the_overlay(self):
        def drop(root):
            rec = json.loads((root / "docs/evidence/prefix_check.json").read_text())
            rec["overlay"] = [e for e in rec["overlay"] if e["path"] != "tests/test_m.py"]
            (root / "docs/evidence/prefix_check.json").write_text(json.dumps(rec))

        self.assertTrue(any("omits" in p for p in self.problems(drop)))

    def test_a_reduced_overlay_is_rejected(self):
        def reduce(root):
            rec = json.loads((root / "docs/evidence/prefix_check.json").read_text())
            rec["overlay"] = rec["overlay"][:1]
            (root / "docs/evidence/prefix_check.json").write_text(json.dumps(rec))

        self.assertTrue(any("omits" in p for p in self.problems(reduce)))

    def test_snapshot_bytes_are_rehashed_from_disk(self):
        def tamper(root):
            (root / ".baseline_x" / "bridge" / "m.py").write_bytes(b"OLD = 2\n")        # manifest says OLD = 1

        self.assertTrue(any("not the snapshot's bytes" in p for p in self.problems(tamper)))

    def test_identities_and_kinds_are_reconstructed_not_trusted(self):
        # fewer tests recorded than the syntax tree defines
        self.assertTrue(self.problems(outcomes={"tests.test_m.A.test_a": "fail"}, tests=1, expected_tests=1,
                                      expected_test_ids=["tests.test_m.A.test_a"]))
        # a lie about the failure kind
        self.assertTrue(any("failure kinds" in p for p in
                            self.problems(failure_kind_by_test={"tests.test_m.A.test_a": "api_missing"})))

    def test_a_repeated_passing_line_is_not_two_tests(self):
        from tools import make_verification_prompt as mvp

        one = "test_a (m.C.test_a) ... ok\n"
        text = one * 3 + "\n" + "-" * 70 + "\nRan 3 tests in 0.1s\n\nOK\n"
        self.assertTrue(any("repeats" in p for p in mvp.log_problems(text, 3)))
        distinct = "".join(f"test_{i} (m.C.test_{i}) ... ok\n" for i in range(3)) + "\nRan 3 tests in 0.1s\n\nOK\n"
        self.assertEqual(mvp.log_problems(distinct, 3), [])
        interleaved = ("test_0 (m.C.test_0) ... noise on stderr\nok\n"
                       "test_1 (m.C.test_1)\nA docstring line ... ok\n"
                       "test_2 (m.C.test_2) ... ok\n\nRan 3 tests in 0.1s\n\nOK\n")
        self.assertEqual(mvp.log_problems(interleaved, 3), [])

    def test_ast_identities_follow_inheritance_and_ignore_helpers(self):
        from tools import prefix_logic

        src = ("import unittest\n\nclass Base(unittest.TestCase):\n    def test_shared(self): pass\n"
               "    def helper(self): pass\n\nclass Child(Base):\n    def test_own(self): pass\n"
               "\nclass NotATest:\n    def test_x(self): pass\n")
        self.assertEqual(prefix_logic.expected_test_ids(src, "m"),
                         {"m.Base.test_shared", "m.Child.test_shared", "m.Child.test_own"})


# =========================================================================== T9
class T9_OnlyDeclaredWhitespaceBetweenTokens(unittest.TestCase):
    P = "*** Begin Patch\\n*** Add File: A.txt\\n+x\\n*** End Patch"

    def setUp(self):
        from tools import apply_patch_attack_test as t

        self.t = t

    def test_python_whitespace_that_javascript_does_not_accept_is_rejected(self):
        for ws in ("\u0085", "\u00a0", "\u2028", "\u3000", "\x0b", "\x1c"):
            body = f'const{ws}r = await tools.apply_patch("{self.P}"); text(r);'
            self.assertIsNone(self.t.parse_wrapper(body)[0], repr(ws))
            body = f'const r = await tools.apply_patch("{self.P}");{ws}text(r);'
            self.assertIsNone(self.t.parse_wrapper(body)[0], repr(ws))

    def test_ordinary_whitespace_is_still_accepted(self):
        for body in (f'const r = await tools.apply_patch("{self.P}");\ntext(r);',
                     f'const\tr =\tawait tools.apply_patch( "{self.P}" ) ;\r\ntext( r );'):
            self.assertIsNotNone(self.t.parse_wrapper(body)[0], repr(body))

    def test_a_lone_raw_cr_inside_a_quoted_literal_is_rejected(self):
        body = 'const r = await tools.apply_patch("*** Begin Patch\r*** End Patch"); text(r);'
        patch, why = self.t.parse_wrapper(body)
        self.assertIsNone(patch, why)


# =========================================================================== T10
class T10_EstablishmentWitnessesAreNotBestEffort(Base):
    def test_a_first_write_whose_backup_cannot_be_written_creates_no_registry(self):
        real = state.atomic_write_text

        def refuse_backup(path, text):
            if Path(path).name == "registry.prev.json":
                raise OSError("simulated: no space")
            return real(path, text)

        with mock.patch.object(state, "atomic_write_text", refuse_backup):
            answer = self.service().execute(CONSULT, "q")
        self.assertIn("PARTIAL SUCCESS", answer)
        self.assertIn("state_not_saved:registry_unwritable", self.w.telemetry()[0]["warnings"])
        self.assertFalse((self.w.state / "registry.json").exists(),
                         "a registry must not exist without its establishment witness")

    def test_register_new_leaves_a_journal_witness_and_a_backup(self):
        rc, out, _ = run_admin(self.w.env(), "new", "--session", "named")
        self.assertEqual(rc, 0, out)
        events = [r.get("event") for r in state.Registry(self.w.state).journal.read()]
        self.assertIn("registry_established", events)
        self.assertTrue((self.w.state / "registry.prev.json").exists())
        (self.w.state / "registry.json").unlink()
        (self.w.state / "instance.json").unlink()
        (self.w.state / "registry.prev.json").unlink()
        exc = expect_error(self, self.service(GPT_ARCHITECT_SESSION="named"))
        self.assertEqual(exc.code, "registry_lost", "the journal is a second witness")

    def test_recovery_advice_does_not_name_a_command_that_cannot_work(self):
        self.service().execute(CONSULT, "q")
        (self.w.state / "registry.json").unlink()
        (self.w.state / "instance.json").unlink()
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "registry_lost")
        self.assertIn("instance.json is missing", str(exc))
        self.assertIn("reinit", str(exc))

    def test_reinit_is_part_of_the_witness_policy(self):
        self.assertIn("registry_reinitialised", state.Registry._ESTABLISHED_EVENTS)      # noqa: SLF001
        self.assertIn("registry_established", state.Registry._ESTABLISHED_EVENTS)        # noqa: SLF001


# =========================================================================== T11 / T12
class T11_OmissionComparesRawBytes(unittest.TestCase):
    def test_files_that_differ_only_in_line_endings_are_not_called_unchanged(self):
        from tools import make_verification_prompt as mvp

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in [*mvp.FULL, *mvp.DIFF_ONLY]:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"line one\nline two {rel}\n".encode())
            snap = root / mvp.SNAPSHOT
            for rel, data in (("bridge/redact.py", b"line one\nline two bridge/redact.py\n"),
                              ("bridge/runner.py", b"line one\r\nline two bridge/runner.py\r\n")):  # CRLF there
                (snap / rel).parent.mkdir(parents=True, exist_ok=True)
                (snap / rel).write_bytes(data)
            old = snap / mvp.DIFF_ONLY[0]
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"older\n")
            prompt, manifest = mvp.assemble(root)
        by_path = {f["path"]: f for f in manifest["files"]}
        self.assertEqual(by_path["bridge/redact.py"]["mode"], "unchanged_since_verified_snapshot")
        self.assertEqual(by_path["bridge/runner.py"]["mode"], "full", "different raw bytes: embedded")


class T12_ProvenanceBreaksAreInvalid(unittest.TestCase):
    def test_a_modified_stage_is_labelled_invalid(self):
        from tools import record_evidence as rec

        with tempfile.TemporaryDirectory() as d:
            root, evidence = Path(d) / "root", Path(d) / "ev"
            (root / "bridge").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "bridge" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
            (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tests" / "test_mut.py").write_text(
                "import os, stat, unittest\nfrom pathlib import Path\n\n\nclass T(unittest.TestCase):\n"
                "    def test_it(self):\n"
                "        target = Path(__file__).resolve().parent.parent / 'bridge' / '__init__.py'\n"
                "        os.chmod(target, stat.S_IWRITE)\n"
                "        target.write_text('X = 2\\n', encoding='utf-8')\n", encoding="utf-8")
            with mock.patch.object(rec, "environment", lambda: {}):
                with self.assertRaises(SystemExit):
                    rec.cmd_tests("mutation", root=root, evidence=evidence)
            record = json.loads((evidence / "tests_mutation.json").read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "INVALID", record["problems"])

    def test_a_failing_test_is_failed_not_invalid(self):
        from tools import record_evidence as rec

        with tempfile.TemporaryDirectory() as d:
            root, evidence = Path(d) / "root", Path(d) / "ev"
            (root / "bridge").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "bridge" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
            (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tests" / "test_bad.py").write_text(
                "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        self.fail('x')\n",
                encoding="utf-8")
            with mock.patch.object(rec, "environment", lambda: {}):
                with self.assertRaises(SystemExit):
                    rec.cmd_tests("failing", root=root, evidence=evidence)
            record = json.loads((evidence / "tests_failing.json").read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "FAILED")


if __name__ == "__main__":
    unittest.main()
