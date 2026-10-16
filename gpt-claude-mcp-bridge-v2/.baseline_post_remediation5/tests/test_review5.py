"""Regression tests for the fifth remediation (GPT round-4 findings S1-S15 and the review's "also" items).

Real child processes (the scripted fake CLI, real Job Objects, real git), no model.  Written so the
module can be run against the PRE-FIX source (tools/prefix_check.py): new-API modules are imported
defensively, so an unfixed tree yields test FAILURES rather than an import error.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, World, WorldCase, passing_unittest_log, write_prefix_evidence

from bridge import admin, codexcli, config, errors, fingerprint, runner, service, state, telemetry

try:                                            # absent in a pre-fix tree
    from bridge import execpath
except ImportError:  # pragma: no cover
    execpath = None

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


# =========================================================================== S1 / S2
class S1_DispositionCommandsNeverSettleProcessFacts(Base):
    def test_reset_keeps_a_raw_running_record_and_the_session_stays_blocked(self):
        reg = self.stale_marker(thread_id=THREAD)                   # no service health check has run
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        kept = reg.read_marker("inflight", self.key())
        self.assertIsNotNone(kept, "reset must not delete the process fact")
        self.assertEqual(kept["state"], "running")
        self.assertIn("RETAINED", out)
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "uncertain_termination")
        self.assertEqual(self.w.exec_entries(), [], "no inference may start against the unresolved process")
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("pending", self.key()), "the discarded disposition stays discarded")
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(self.w.exec_entries()[0]["kind"], "exec_fresh", "a fresh session: the pointer was retired")

    def test_adopt_keeps_a_raw_running_record_and_the_session_stays_blocked(self):
        reg = self.stale_marker(thread_id=THREAD)
        rc, out, _ = run_admin(self.w.env(), "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual((reg.read_marker("inflight", self.key()) or {}).get("state"), "running")
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        self.assertEqual(self.w.exec_entries(), [])
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(self.w.exec_entries()[0]["kind"], "exec_resume")

    def test_a_pure_thread_debt_record_is_still_removed_by_reset(self):
        reg = self.stale_marker(state="persistence_failed", thread_id=THREAD, unbound_thread=THREAD)
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("inflight", self.key()), "persistence_failed is only a thread debt")

    def test_a_settlement_that_cannot_be_persisted_is_reported(self):
        self.stale_marker(thread_id=THREAD)
        with mock.patch.object(state.Registry, "update_marker", return_value=False):
            rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 1)
        self.assertIn("COULD NOT persist", out)


class S2_ASettledDispositionStaysSettled(Base):
    def test_reset_of_an_uncertain_fresh_call_is_not_rederived_by_reconcile(self):
        reg = self.stale_marker(thread_id=THREAD)
        expect_error(self, self.service())                          # converted: uncertain + unbound_thread
        self.assertEqual(reg.read_marker("pending", self.key())["thread_id"], THREAD)
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertIsNone(reg.read_marker("pending", self.key()),
                          "the thread id stayed on the record but its disposition was discarded durably")
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_a_previously_bound_uncertain_call_is_not_rederived_after_the_pointer_is_retired(self):
        reg = self.registry()
        run_admin(self.w.env(), "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        marker = {"state": "uncertain", "reason": "service_children_unobservable", "phase": "completed_unbound",
                  "call_id": "d" * 8, "owner_pid": 2_000_000_000, "owner_identity": "1", "thread_id": THREAD,
                  "resumed": False, "pointer_bound": True, "model": "gpt-6.1-sol", "ownership": "job_object"}
        self.assertTrue(reg.write_marker("inflight", self.key(), marker))
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("pending", self.key()), "reset discarded this thread's disposition")

    def test_thread_debt_honours_a_recorded_settlement_only_for_that_thread(self):
        marker = {"state": "uncertain", "resumed": False, "thread_id": THREAD, "thread_settled": THREAD}
        self.assertIsNone(state.thread_debt(marker, None))
        self.assertEqual(state.thread_debt({**marker, "thread_settled": THREAD2}, None), THREAD)
        self.assertEqual(state.thread_debt({**marker, "thread_id": THREAD2, "thread_settled": THREAD}, None),
                         THREAD2, "a different thread is a different debt")


# =========================================================================== S3
class S3_ThePrimaryWriteIsCheckedBeforeMirroring(Base):
    def test_an_unpersisted_uncertainty_is_never_mirrored_into_a_pending_marker(self):
        reg = self.stale_marker(thread_id=THREAD)
        real = state.Registry.update_marker

        def failing(self_, kind, key, **fields):
            if kind == "inflight" and fields.get("state") == "uncertain":
                return False
            return real(self_, kind, key, **fields)

        with mock.patch.object(state.Registry, "update_marker", failing):
            exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "uncertainty_marker_unwritable")
        self.assertIsNone(reg.read_marker("pending", self.key()),
                          "pending state must not be written as though the process fact were durable")
        self.assertEqual(reg.read_marker("inflight", self.key())["state"], "running", "the raw record is intact")
        self.assertEqual(self.w.exec_entries(), [])
        # once state is writable again the record converts normally
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        self.assertEqual(reg.read_marker("inflight", self.key())["state"], "uncertain")

    def test_reset_after_an_unpersisted_conversion_cannot_make_the_call_runnable(self):
        reg = self.stale_marker(thread_id=THREAD)
        with mock.patch.object(state.Registry, "update_marker", return_value=False):
            expect_error(self, self.service())
        run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        self.assertEqual(self.w.exec_entries(), [])
        self.assertIsNotNone(reg.read_marker("inflight", self.key()))


# =========================================================================== S4
class S4_AReaderThatDidNotFinishIsIncompleteEvidence(Base):
    def test_a_reader_still_alive_after_its_join_withholds_the_answer(self):
        release = threading.Event()
        self.addCleanup(release.set)
        real_feed = runner.Events.feed

        def blocking(events, raw):
            if b"LATE-ERROR" in raw:
                release.wait(30)                       # processing stalls after turn.completed
            return real_feed(events, raw)

        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    {"type": "turn.completed"},
                                    {"type": "error", "message": "LATE-ERROR"}]})
        with mock.patch.object(runner.Events, "feed", blocking), \
                mock.patch.object(runner, "READER_JOIN_TIMEOUT_S", 0.5, create=True):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.OUTPUT_INCOMPLETE, "turn_evidence_incomplete"))
        self.assertEqual(self.w.telemetry()[0]["infra"]["reader_failed"], 1)

    def test_turn_problems_names_every_reason_a_review_cannot_be_relied_on(self):
        tp = getattr(runner, "turn_problems", None)
        self.assertIsNotNone(tp, "bridge.runner.turn_problems is the shared validity predicate")

        def result(**ev_fields):
            ev = runner.Events()
            ev.turn_completed = True
            ev.stdout_eof = True
            for k, v in ev_fields.items():
                setattr(ev, k, v)
            return runner.RunResult(returncode=0, timed_out=False, cancelled=False, events=ev, stderr_tail="",
                                    duration_s=1.0, cleanup_ok=True, termination="not_needed")

        self.assertEqual(tp(result()), [])
        for field, reason in (("reader_failed", "stdout_not_read_completely"),
                              ("stdout_eof", "stdout_not_read_completely"),
                              ("errors_after_completion", "error_after_completion"),
                              ("evidence_truncated_items", "evidence_truncated"),
                              ("infra_failed_items", "infrastructure_failure_in_turn"),
                              ("thread_conflict", "thread_identity_conflict"),
                              ("turn_failed", "turn_failed")):
            value = False if field == "stdout_eof" else (True if isinstance(getattr(runner.Events(), field), bool) else 1)
            self.assertIn(reason, tp(result(**{field: value})), field)


# =========================================================================== S5
class S5_OraclesAttributeOperationsNotText(unittest.TestCase):
    def setUp(self):
        from tools import smoke_oracles

        self.o = smoke_oracles
        self.head = "0123456789abcdef0123456789abcdef01234567"
        self.name = "WRITE_ATTEMPT_FRESH.txt"

    @staticmethod
    def native(i, command, output, name="shell"):
        return [{"payload": {"type": "function_call", "call_id": f"n{i}", "name": name,
                             "arguments": json.dumps({"command": command})}},
                {"payload": {"type": "function_call_output", "call_id": f"n{i}", "output": output}}]

    @staticmethod
    def js(i, body, output):
        return [{"payload": {"type": "custom_tool_call", "call_id": f"j{i}", "name": "exec", "input": body}},
                {"payload": {"type": "custom_tool_call_output", "call_id": f"j{i}", "output": output}}]

    def records(self, *rows):
        flat = [r for group in rows for r in group]
        return self.o.command_records(flat)

    def test_printing_a_command_and_a_hash_is_not_a_control(self):
        forged = self.records(self.js(0, f'text("git rev-parse HEAD {self.head}");', f"git rev-parse HEAD {self.head}"))
        self.assertFalse(self.o.control_executed(forged, self.head))
        forged2 = self.records(self.native(0, ["echo", f"git rev-parse HEAD {self.head}"], self.head))
        self.assertFalse(self.o.control_executed(forged2, self.head))
        real = self.records(self.native(0, ["git", "rev-parse", "HEAD"], self.head + "\n"))
        self.assertTrue(self.o.control_executed(real, self.head))
        passthrough = self.records(self.js(1, 'text(await tools.exec_command({cmd: "git rev-parse HEAD"}));',
                                           self.head + "\n"))
        self.assertTrue(self.o.control_executed(passthrough, self.head))

    def test_a_printed_refusal_is_not_a_blocked_write(self):
        o = self.o
        forged = self.records(self.js(0, 'text("canary.txt TAMPERED rejected: Access is denied");',
                                      "canary.txt TAMPERED rejected: Access is denied"))
        steps = o.probe_steps(forged, self.name)
        self.assertFalse(steps["append_canary"]["attempted"])
        self.assertEqual(o.judge_step(**steps["append_canary"], filesystem_changed=False)[0], "INCONCLUSIVE")

    def test_a_read_that_is_denied_is_not_a_denied_write(self):
        o = self.o
        read = self.records(self.native(0, ["type", "canary.txt"], "Access is denied."))
        steps = o.probe_steps(read, self.name)
        self.assertFalse(steps["append_canary"]["attempted"])
        mention = self.records(self.native(0, ["powershell", "-Command", "Get-Content canary.txt  # TAMPERED"],
                                           "Access is denied."))
        self.assertFalse(o.probe_steps(mention, self.name)["append_canary"]["attempted"])

    def test_a_command_that_supplies_the_refusal_wording_itself_proves_nothing(self):
        o = self.o
        echoed = self.records(self.native(
            0, 'echo "Access is denied" ; echo TAMPERED >> canary.txt', "Access is denied\n"))
        step = o.probe_steps(echoed, self.name)["append_canary"]
        self.assertTrue(step["attempted"])
        self.assertFalse(step["refusal_recorded"], "the wording came from the command text")

    def test_genuine_attributed_writes_are_recognised(self):
        o = self.o
        rows = self.records(
            self.native(0, ["powershell", "-Command", f"Set-Content -Path {self.name} -Value WRITE-PROBE"],
                        "Set-Content : Access to the path is denied. Access is denied"),
            self.native(1, 'echo TAMPERED >> canary.txt', "Access is denied."),
            self.js(2, f'text(await tools.apply_patch("*** Begin Patch\\n*** Add File: {self.name}.patch.txt\\n'
                       '+x\\n*** End Patch"));',
                    "patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings"),
            self.native(3, ["git", "commit", "--allow-empty", "-m", "probe"], "fatal: Unable to create index.lock: "
                                                                          "Permission denied"),
        )
        steps = o.probe_steps(rows, self.name)
        for step in ("create_file", "append_canary", "apply_patch_file", "git_commit"):
            self.assertEqual(o.judge_step(**steps[step], filesystem_changed=False)[0], "PASS", step)

    def test_wrappers_with_logic_or_several_calls_are_not_attributed(self):
        o = self.o
        for body in ('const x = "git rev-parse HEAD"; text(x);',
                     'text(await tools.exec_command({cmd: "git rev-parse HEAD"})); text("extra");',
                     'if (true) { text(await tools.exec_command({cmd: "git rev-parse HEAD"})); }',
                     'text(await tools.exec_command({cmd: "git rev-parse " + "HEAD"}));'):
            recs = self.records(self.js(0, body, self.head))
            self.assertFalse(o.control_executed(recs, self.head), body)


# =========================================================================== S6
class S6_OutputScanningHasNoSilentBlindSpots(unittest.TestCase):
    @staticmethod
    def item(**fields):
        return {"type": "command_execution", "status": "failed", "exit_code": 1, **fields}

    def test_request_named_keys_inside_returned_output_are_scanned(self):
        for container in ("result", "output", "content", "error"):
            item = self.item(**{container: {"input": {"error": HELPER}}})
            self.assertIn("sandbox_setup", runner.classify_item(item, None)[1], container)
        nested = self.item(result={"arguments": [{"command": {"stderr": HELPER}}]})
        self.assertIn("sandbox_setup", runner.classify_item(nested, None)[1])

    def test_the_request_itself_is_still_not_evidence(self):
        item = self.item(command="git grep helper_unknown_error", aggregated_output="fatal: nothing")
        self.assertEqual(runner.classify_item(item, None)[1], [])
        item = self.item(call={"command": "git grep helper_unknown_error"}, aggregated_output="")
        self.assertEqual(runner.classify_item(item, None)[1], [])

    def test_json_whitespace_escapes_in_an_oversized_line_are_resolved(self):
        for raw in (b"CreateProcess\\n{", b"CreateProcess\\r\\n  {", b"CreateProcess\\t{", b"CreateProcess\\u0020{"):
            scan = runner.OversizeScan(b"A" * 5000 + raw + b"B" * 100)
            self.assertIn("process_creation", scan.kinds, raw)
        half = runner.OversizeScan(b"A" * 100 + b"CreateProcess\\")
        half.feed(b"n{" + b"B" * 100)
        self.assertIn("process_creation", half.kinds, "an escape split across a chunk boundary")

    def test_unbounded_whitespace_cannot_make_the_scan_quadratic(self):
        blob = ("CreateProcess" + " " * 3000) * 4000
        started = time.monotonic()
        runner.signature_kinds(blob)
        self.assertLess(time.monotonic() - started, 5.0)


# =========================================================================== S7
@unittest.skipUnless(WIN, "Windows system tools")
class S7_SystemToolsFailClosed(unittest.TestCase):
    def _failing_os_query(self):
        import ctypes

        fake = mock.Mock()
        fake.kernel32.GetSystemDirectoryW.return_value = 0
        return mock.patch.object(ctypes, "windll", fake, create=True)

    def test_no_environment_fallback_when_the_os_query_fails(self):
        with self._failing_os_query(), mock.patch.dict(os.environ, {"SystemRoot": r"C:\Evil", "windir": r"C:\Evil"}):
            unavailable = getattr(execpath, "SystemDirectoryUnavailable", None)
            self.assertIsNotNone(unavailable, "execpath.SystemDirectoryUnavailable")
            with self.assertRaises(unavailable):
                execpath.system_tool("taskkill")

    def test_kill_tree_never_launches_a_path_built_from_the_environment(self):
        launched: list[list[str]] = []

        class Proc:
            pid = 1234
            returncode = None

            def poll(self):
                return None

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 0

        with self._failing_os_query(), mock.patch.dict(os.environ, {"SystemRoot": r"C:\Evil", "windir": r"C:\Evil"}), \
                mock.patch.object(subprocess, "run", lambda argv, *a, **k: launched.append(list(argv))):
            codexcli.kill_tree(Proc())
            self.assertEqual(codexcli.running_lock_holders(), [])
        self.assertFalse([a for a in launched if "evil" in " ".join(a).lower()], launched)


# =========================================================================== S8
class S8_WrapperGrammarRejectsInvalidJavaScript(unittest.TestCase):
    P = "*** Begin Patch\\n*** Add File: A.txt\\n+x\\n*** End Patch"

    def setUp(self):
        from tools import apply_patch_attack_test as t

        self.t = t

    def test_reserved_words_cannot_be_binding_identifiers(self):
        for word in ("null", "true", "class", "function", "new", "typeof", "this"):
            for body in (f'const {word} = "{self.P}"; const r = await tools.apply_patch({word}); text(r);',
                         f'const {word} = await tools.apply_patch("{self.P}"); text({word});'):
                self.assertIsNone(self.t.parse_wrapper(body)[0], body)
        self.assertIsNotNone(self.t.parse_wrapper(f'const p = "{self.P}"; const r = await tools.apply_patch(p); '
                                                  'text(r);')[0])

    def test_a_raw_carriage_return_inside_a_quoted_string_is_not_attested(self):
        for quote in ('"', "'"):
            body = f'const r = await tools.apply_patch({quote}*** Begin Patch\r\n*** End Patch{quote}); text(r);'
            self.assertIsNone(self.t.parse_wrapper(body)[0], quote)
        # a template literal may span lines; the language normalises its raw CRLF to LF
        template = "const r = await tools.apply_patch(`*** Begin Patch\r\n*** End Patch`); text(r);"
        self.assertEqual(self.t.parse_wrapper(template)[0], "*** Begin Patch\n*** End Patch")

    def test_an_escaped_cr_is_still_a_cr(self):
        body = 'const r = await tools.apply_patch("a\\r\\nb"); text(r);'
        self.assertEqual(self.t.parse_wrapper(body)[0], "a\r\nb")


# =========================================================================== S9 / S10
class S9_StagedExecutionIsFingerprintedAfterTheRun(unittest.TestCase):
    def test_run_problems_rejects_a_staged_copy_that_changed(self):
        from tools import record_evidence as rec

        ok = dict(before="a", after="a", exit_code=0, skipped=0, failing=[], tests_run=3, staged="a",
                  staged_after="a")
        self.assertEqual(rec.run_problems(**ok), [])
        self.assertTrue(any("staged copy was modified" in p for p in rec.run_problems(**{**ok, "staged_after": "b"})))
        self.assertTrue(any("staged copy was modified" in p for p in rec.run_problems(**{**ok, "staged_after": None})))

    def test_a_test_that_rewrites_a_staged_module_invalidates_the_record(self):
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
        self.assertNotEqual(record["outcome"], "OK")
        self.assertTrue(any("staged copy was modified" in p for p in record["problems"]), record["problems"])
        self.assertNotEqual(record["source_sha256_staged_copy_after_run"], record["source_sha256_staged_copy"])

    def test_an_unmodified_stage_records_equal_fingerprints_and_read_only_files(self):
        from tools import record_evidence as rec

        with tempfile.TemporaryDirectory() as d:
            root, evidence = Path(d) / "root", Path(d) / "ev"
            (root / "bridge").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "bridge" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
            (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tests" / "test_ok.py").write_text(
                "import os, unittest\nfrom pathlib import Path\n\n\nclass T(unittest.TestCase):\n"
                "    def test_stage_is_read_only(self):\n"
                "        target = Path(__file__).resolve().parent.parent / 'bridge' / '__init__.py'\n"
                "        self.assertFalse(os.access(target, os.W_OK))\n", encoding="utf-8")
            with mock.patch.object(rec, "environment", lambda: {}):
                rec.cmd_tests("clean", root=root, evidence=evidence)
            record = json.loads((evidence / "tests_clean.json").read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "OK", record)
        self.assertEqual(record["source_sha256_staged_copy_after_run"], record["source_sha256_staged_copy"])
        self.assertTrue(record["staged_copy_read_only"])


class S10_PrefixEvidenceIsCheckableEndToEnd(unittest.TestCase):
    SHA = "a" * 64

    def problems(self, **overrides) -> list[str]:
        from tools import make_verification_prompt as mvp

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            write_prefix_evidence(tmp, self.SHA, **overrides)
            return mvp.prefix_problems(tmp, self.SHA)

    def test_a_consistent_record_is_accepted(self):
        self.assertEqual(self.problems(), [])

    def test_each_break_in_the_chain_is_rejected(self):
        cases = {
            "stale": dict(source_combined_sha256="b" * 64),
            "log hash": dict(raw_log_sha256="0" * 64),
            "outcomes vs log": dict(outcomes={"tests.t.A.test_a": "fail", "tests.t.A.test_b": "fail"}),
            "count": dict(expected_tests=3),
            "loader": dict(loader_errors=["unittest.loader._FailedTest.x"]),
            "undeclared": dict(undeclared_passes=["tests.t.A.test_b"]),
            "skipped": dict(skipped=["tests.t.A.test_b"]),
            "overlay problems": dict(overlay_problems=["x"]),
            "snapshot hash": dict(snapshot_manifest_combined_sha256_all_files="z" * 64),
            "snapshot file": dict(overlay=[{"path": "bridge/m.py", "sha256": "0" * 64, "origin": "snapshot"}]),
            "harness file": dict(overlay=[{"path": "tests/helpers.py", "sha256": "0" * 64,
                                           "origin": "current_tree"}]),
        }
        for name, change in cases.items():
            self.assertTrue(self.problems(**change), name)

    def test_the_gate_accounts_for_each_test_not_just_the_footer(self):
        from tools import make_verification_prompt as mvp

        self.assertEqual(mvp.log_problems(passing_unittest_log(5).decode(), 5), [])
        self.assertTrue(mvp.log_problems("Ran 5 tests in 1.0s\n\nOK\n", 5), "a bare footer is not accounting")
        fewer = passing_unittest_log(4).decode().replace("Ran 4 tests", "Ran 5 tests")
        self.assertTrue(any("per-test" in p for p in mvp.log_problems(fewer, 5)))

    def test_api_errors_are_kept_distinct_from_assertions(self):
        from tools import prefix_check

        log = ("======================================================================\n"
               "ERROR: test_a (tests.t.A.test_a)\n"
               "----------------------------------------------------------------------\n"
               "Traceback (most recent call last):\n  File \"x\", line 1, in test_a\n"
               "AttributeError: module 'bridge.execpath' has no attribute 'trusted_target'\n\n"
               "======================================================================\n"
               "ERROR: test_b (tests.t.A.test_b)\n"
               "----------------------------------------------------------------------\n"
               "Traceback (most recent call last):\n  File \"x\", line 1, in test_b\n"
               "KeyError: 'state'\n\n"
               "----------------------------------------------------------------------\nRan 3 tests in 0.1s\n")
        kinds = prefix_check.classify_errors(log, {"tests.t.A.test_a": "error", "tests.t.A.test_b": "error",
                                                   "tests.t.A.test_c": "fail", "tests.t.A.test_d": "pass"})
        self.assertEqual(kinds, {"tests.t.A.test_a": "api_missing", "tests.t.A.test_b": "behavioural_error",
                                 "tests.t.A.test_c": "assertion"})


# =========================================================================== S11
class S11_NoExecutionRefusalsShareOneGuardedCleanup(Base):
    def _fail_exec_only(self):
        real = subprocess.Popen

        def fail(argv, *a, **k):
            if "exec" in list(argv):
                raise OSError("simulated: the CLI could not be started")
            return real(argv, *a, **k)

        return mock.patch.object(subprocess, "Popen", fail)

    def test_spawn_refusal_survives_a_marker_that_cannot_be_deleted(self):
        real_clear = state.Registry.clear_marker

        def deny(self_, kind, key):
            if kind == "inflight":
                raise PermissionError("denied")
            return real_clear(self_, kind, key)

        with self._fail_exec_only(), mock.patch.object(state.Registry, "clear_marker", deny):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SUBPROCESS_FAILED, "spawn_failed"))
        self.assertFalse(exc.inference_may_have_started)
        rec = self.w.telemetry()[0]
        self.assertFalse(rec["inference_may_have_started"])
        self.assertEqual(rec["history"], "no_turn_started")
        marker = state.Registry(self.w.state).read_marker("inflight", self.key())
        self.assertEqual((marker or {}).get("state"), "refused_before_exec")
        self.assertEqual(self.service().execute(CONSULT, "works now"), "FAKE ANSWER")

    def test_a_cleanup_that_fails_completely_is_reported_and_does_not_replace_the_refusal(self):
        quiet = [{"name": "idea", "source": "config"}]
        self.w.set(mcp_sequence=[quiet, quiet, quiet + [{"name": "sneaky", "source": "config"}]])
        real_clear, real_write = state.Registry.clear_marker, state.Registry.write_marker

        def deny(self_, kind, key):
            if kind == "inflight":
                raise PermissionError("denied")
            return real_clear(self_, kind, key)

        def refuse_fallback(self_, kind, key, payload):
            if kind == "inflight" and payload.get("state") == "refused_before_exec":
                return False
            return real_write(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "clear_marker", deny), \
                mock.patch.object(state.Registry, "write_marker", refuse_fallback):
            exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "mcp_changed_before_exec")
        self.assertIn("refusal_marker_not_cleared", self.w.telemetry()[0]["warnings"])
        self.assertIn("could neither be removed nor marked", str(exc))      # S12: visible with the error
        self.assertFalse(exc.inference_may_have_started)


# =========================================================================== S12
class S12_ErrorExitsCarryTheRecordedWarnings(Base):
    def test_an_unexpected_server_and_a_failing_turn_are_both_visible(self):
        quiet = [{"name": "idea", "source": "config"}]
        # audit(2 reads) + pre-exec recheck + post-turn recheck shows a server nobody disabled
        self.w.set(mcp_sequence=[quiet, quiet, quiet, quiet + [{"name": "appeared", "source": "config"}]],
                   exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    {"type": "item.completed", "item": {
                                        "type": "command_execution", "status": "failed", "exit_code": 1,
                                        "aggregated_output": HELPER}},
                                    {"type": "turn.completed"}]})
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "turn_sandbox_setup_failed")
        text = str(exc)
        self.assertIn("appeared", text, "the exposure recorded before the failure must be in the error text")
        self.assertIn("[bridge-warning]", text)

    def test_the_warning_block_appears_exactly_when_warnings_were_recorded(self):
        self.w.set(exec={"exit": 1, "events": [{"type": "turn.failed", "error": {"message": "boom"}}]})
        exc = expect_error(self, self.service())
        recorded = self.w.telemetry()[0]["warnings"]
        self.assertEqual("were recorded before this error" in str(exc), bool(recorded), recorded)
        self.setUp()
        self.w.set(exec={"exit": 1, "events": [{"type": "turn.failed", "error": {"message": "boom"}}]})
        exc = expect_error(self, self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0"))
        self.assertIn("preflight is DISABLED", str(exc), "a recorded banner accompanies the error")


# =========================================================================== S13
class S13_OneCallRegistryLossIsStillLoss(Base):
    def test_loss_after_a_single_successful_call_is_refused(self):
        self.service().execute(CONSULT, "only call")
        self.assertTrue((self.w.state / "registry.prev.json").exists(), "establishment evidence after one write")
        (self.w.state / "registry.json").unlink()
        (self.w.state / "instance.json").unlink()
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_CORRUPT, "registry_lost"))
        self.assertEqual(len(self.w.exec_entries()), 1, "no replacement thread may start")

    def test_the_lifecycle_journal_is_a_second_witness(self):
        self.service().execute(CONSULT, "only call")
        for name in ("registry.json", "instance.json", "registry.prev.json"):
            (self.w.state / name).unlink(missing_ok=True)       # (a pre-fix tree never wrote the backup)
        self.assertTrue((self.w.state / "journal.jsonl").exists())
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "registry_lost")

    def test_a_directory_that_merely_holds_locks_is_not_an_established_registry(self):
        reg = self.registry()                                # creates locks/inflight/pending directories
        self.assertEqual(reg.snapshot()["sessions"], {})


# =========================================================================== S14
class S14_TheSenderUsesThePackagesValidityPredicate(unittest.TestCase):
    def setUp(self):
        try:
            from tools import send_verification
        except ImportError:                                  # pre-fix tree
            send_verification = None
        self.sv = send_verification

    def _result(self, **ev_fields):
        ev = runner.Events()
        ev.turn_completed, ev.stdout_eof = True, True
        for k, v in ev_fields.items():
            setattr(ev, k, v)
        return runner.RunResult(returncode=0, timed_out=False, cancelled=False, events=ev, stderr_tail="",
                                duration_s=1.0, cleanup_ok=True, termination="not_needed")

    def test_completion_and_an_answer_are_not_enough(self):
        self.assertIsNotNone(self.sv, "tools/send_verification.py")
        ok, why = self.sv.review_is_valid(self._result(), "an answer")
        self.assertTrue(ok, why)
        for bad in (dict(reader_failed=True), dict(errors_after_completion=1), dict(evidence_truncated_items=2),
                    dict(thread_conflict=True), dict(infra_failed_items=1), dict(stdout_eof=False)):
            ok, why = self.sv.review_is_valid(self._result(**bad), "an answer")
            self.assertFalse(ok, bad)
            self.assertTrue(why, bad)
        ok, why = self.sv.review_is_valid(self._result(), "   ")
        self.assertFalse(ok)
        self.assertIn("no_answer", why)

    def test_it_refuses_without_the_allowance_acknowledgement(self):
        self.assertIsNotNone(self.sv)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.sv.main([]), 2)


# =========================================================================== S15
class S15_ExactBannersAndFlagReporting(Base):
    def test_a_forgotten_state_not_saved_code_is_not_hidden_by_a_real_persistence_banner(self):
        real_warn = service._Ctx.warn

        def forgetful(ctx, code, banner):
            real_warn(ctx, code, banner)
            if code.startswith("sandbox_preflight_skipped"):
                ctx.warnings.append("state_not_saved:forgotten_example")

        with mock.patch.object(service._Ctx, "warn", forgetful), \
                mock.patch.object(state.Registry, "bind_thread",
                                  side_effect=errors.BridgeError(errors.STATE_ERROR, "x", code="registry_unwritable")):
            answer = self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0").execute(CONSULT, "q")
        self.assertIn("PARTIAL SUCCESS", answer)
        self.assertIn("Recorded warning: state_not_saved:forgotten_example.", answer)

    def test_a_retained_maintenance_flag_is_reported_when_the_action_raises(self):
        with mock.patch.object(state.Registry, "quarantine", side_effect=OSError("disk")), \
                mock.patch.object(state.Registry, "end_maintenance", return_value=False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(OSError):
                admin.main(["quarantine", "--yes"], self.w.env())
        self.assertIn("end-maintenance", out.getvalue())

    def test_a_probe_failure_reports_a_retained_flag_too(self):
        reg = self.registry()
        with mock.patch.object(state.Registry, "active_sessions", side_effect=OSError("probe")), \
                mock.patch.object(state.Registry, "end_maintenance", return_value=False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(OSError):
                admin._require_quiescent(reg, "x")
        self.assertIn("end-maintenance", out.getvalue())


# =========================================================================== also (review's other items)
class Also_CompletenessOfConfigurationReadouts(unittest.TestCase):
    def test_a_truncated_capture_is_not_a_complete_readout(self):
        script = "import sys; sys.stdout.write('x' * 300000)"
        with mock.patch.object(codexcli, "_SIMPLE_MAX_BYTES", 1000):
            res = codexcli.run_simple([sys.executable, "-c", script], dict(os.environ), timeout_s=60)
        self.assertTrue(getattr(res, "truncated", False))
        self.assertFalse(getattr(res, "complete", True))
        full = codexcli.run_simple([sys.executable, "-c", "print('ok')"], dict(os.environ), timeout_s=60)
        self.assertTrue(full.complete)

    def test_mcp_listing_fails_closed_on_an_incomplete_capture(self):
        incomplete = codexcli.Simple(0, "[]", "", False, truncated=True)
        with mock.patch.object(codexcli, "run_simple", return_value=incomplete):
            with self.assertRaises(errors.BridgeError) as cm:
                codexcli._list_mcp(codexcli.Cli(("codex",), "native", "codex"), {}, ".", [])
        self.assertEqual(cm.exception.code, "mcp_list_unavailable")

    def test_allowed_servers_enabled_only_in_the_discovery_list_are_still_accumulated(self):
        calls = {"n": 0}

        def listing(cli, env, cwd, extra):
            calls["n"] += 1
            # the first readout shows the allow-listed server enabled; the final one shows it disabled
            return [{"name": "docs", "enabled": calls["n"] == 1}]

        features = {g: True for g in (*codexcli.REQUIRED_FEATURE_GATES, *codexcli.OPTIONAL_FEATURE_GATES)}
        with mock.patch.object(codexcli, "_list_mcp", listing), \
                mock.patch.object(codexcli, "load_features", return_value={g: False for g in features}):
            audit = codexcli.capability_audit(codexcli.Cli(("codex",), "native", "codex"), {}, Path("."),
                                              frozenset({"docs"}), features)
        self.assertEqual(audit.allowed_servers, ["docs"])


class Also_UnchangedFilesAreListedNotRepeated(unittest.TestCase):
    def test_a_file_identical_to_the_verified_snapshot_is_hash_listed_unless_the_review_asked_for_it(self):
        from tools import make_verification_prompt as mvp

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in [*mvp.FULL, *mvp.DIFF_ONLY]:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"content of {rel}\n", encoding="utf-8")
            snap = root / mvp.SNAPSHOT
            for rel in ("bridge/redact.py", "tests/test_service.py"):            # identical in the snapshot
                (snap / rel).parent.mkdir(parents=True, exist_ok=True)
                (snap / rel).write_text(f"content of {rel}\n", encoding="utf-8")
            old = snap / mvp.DIFF_ONLY[0]
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text("an older version\n", encoding="utf-8")
            prompt, manifest = mvp.assemble(root)
        by_path = {f["path"]: f for f in manifest["files"]}
        self.assertEqual(by_path["bridge/redact.py"]["mode"], "unchanged_since_verified_snapshot")
        self.assertNotIn("=== FILE: bridge/redact.py ===", prompt)
        self.assertEqual(by_path["bridge/runner.py"]["mode"], "full", "no snapshot copy: embedded")
        self.assertIn("tests/test_service.py", mvp.ALWAYS_FULL)
        self.assertEqual(by_path["tests/test_service.py"]["mode"], "full")
        self.assertIn("=== FILE: tests/test_service.py ===", prompt)


class Also_TestSuiteHygiene(unittest.TestCase):
    def test_no_bare_git_launch_remains_in_any_tool_or_test(self):
        """A bare `git` is resolved from the current directory first.  Every launch in tools/ and tests/
        uses the absolute trusted executable; the only literals are argv *comparisons* and fixtures."""
        offenders = []
        pattern = re.compile(r'(?:subprocess\.(?:run|Popen|check_output|call)|run_simple|_git|Popen)\(\s*\[\s*"git"')
        for path in sorted((ROOT / "tools").glob("*.py")) + sorted((ROOT / "tests").glob("*.py")):
            if path.name == "test_review5.py":
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
