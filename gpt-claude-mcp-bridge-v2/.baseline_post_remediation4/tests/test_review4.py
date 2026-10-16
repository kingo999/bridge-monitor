"""Regression tests for the fourth remediation (GPT round-3 findings R1-R13 and retained items).

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
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, World, WorldCase

from bridge import admin, codexcli, config, errors, fingerprint, gitsafe, jobobject, runner, service, state, telemetry

try:                                            # absent in a pre-fix tree
    from bridge import execpath
except ImportError:  # pragma: no cover
    execpath = None

CONSULT = config.CONSULT
WIN = sys.platform == "win32"
THREAD = "aaaaaaaa-1111-2222-3333-444444444444"
HELPER = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"
POLICY = "patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings"


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


# =========================================================================== R1
class R1_ThreadDebtIsDerivedOnEveryPath(Base):
    def test_reconcile_on_a_raw_stale_record_keeps_the_fresh_threads_debt(self):
        for phase in ("running", "completed_unbound"):
            self.setUp()
            reg = self.stale_marker(thread_id=THREAD, phase=phase)         # no service health check ran
            rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
            self.assertEqual(rc, 0, out)
            self.assertIsNone(reg.read_marker("inflight", self.key()))
            self.assertEqual((reg.read_marker("pending", self.key()) or {}).get("thread_id"), THREAD, phase)
            self.assertEqual(expect_error(self, self.service()).code, "pending_persistence", phase)
            self.assertEqual(self.w.exec_entries(), [])

    @unittest.skipUnless(WIN, "an interrupted turn is only 'in doubt' where service children are unobservable")
    def test_an_interrupted_fresh_turn_that_announced_a_thread_leaves_a_debt(self):
        self.w.set(exec={"sleep_after_events": 30})        # thread.started is printed, then the turn hangs
        exc = expect_error(self, self.service(GPT_ARCHITECT_TIMEOUT="2"))
        self.assertEqual(exc.category, errors.TIMEOUT)
        reg = state.Registry(self.w.state)
        marker = reg.read_marker("inflight", self.key())
        self.assertEqual(marker["state"], "uncertain")
        thread = marker["thread_id"]
        self.assertEqual(marker.get("unbound_thread"), thread, "the debt must be recorded in the primary record")
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual((reg.read_marker("pending", self.key()) or {}).get("thread_id"), thread)
        self.assertEqual(expect_error(self, self.service()).code, "pending_persistence")

    def test_a_resumed_call_never_leaves_a_debt(self):
        marker = {"state": "uncertain", "resumed": True, "thread_id": THREAD}
        self.assertIsNone(state.thread_debt(marker, None))
        self.assertEqual(state.thread_debt({**marker, "resumed": False}, None), THREAD)
        self.assertIsNone(state.thread_debt({**marker, "resumed": False}, THREAD), "bound by the pointer")
        self.assertEqual(state.thread_debt({"state": "uncertain", "unbound_thread": THREAD}, None), THREAD)
        self.assertIsNone(state.thread_debt({"state": "uncertain", "thread_id": "x\n" + THREAD}, None))

    def test_reset_and_adopt_recognise_a_raw_stale_record_too(self):
        reg = self.stale_marker(thread_id=THREAD)
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("inflight", self.key()), "reset discards the thread debt of a raw record")
        reg = self.stale_marker(thread_id=THREAD)
        rc, out, _ = run_admin(self.w.env(), "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(reg.read_marker("inflight", self.key()))
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")
        self.assertEqual(self.w.exec_entries()[0]["kind"], "exec_resume")


# =========================================================================== R2
class R2_ProcessUncertaintyIsDurableFirst(Base):
    def _in_doubt_call(self, **patches):
        with mock.patch.object(jobobject.ProcessOwnership, "active_processes", lambda owner: 1), \
                mock.patch.object(jobobject.ProcessOwnership, "terminate", lambda owner: None), \
                mock.patch.object(runner, "EXIT_ACCOUNTING_GRACE_S", 0.05):
            return self.service().execute(CONSULT, "q")

    def test_a_failed_uncertain_write_is_not_masked_by_a_later_phase_update(self):
        real = state.Registry.write_marker
        failures = {"n": 0}

        def flaky(self_, kind, key, payload):
            if kind == "inflight" and payload.get("state") == "uncertain" and failures["n"] < 3:
                failures["n"] += 1
                return False
            return real(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "write_marker", flaky):
            answer = self._in_doubt_call()
        self.assertIn("FAKE ANSWER", answer)
        marker = state.Registry(self.w.state).read_marker("inflight", self.key())
        self.assertEqual(marker["state"], "uncertain",
                         "the process fact must survive a failed write followed by a successful phase update")
        self.assertIn("uncertainty_marker_unwritable", self.w.telemetry()[0]["warnings"])
        self.assertIn("uncertainty_marker_unwritable", answer)          # never telemetry-only
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")

    def test_recovery_makes_the_process_fact_durable_before_the_thread_debt(self):
        self.stale_marker(thread_id=THREAD)
        order: list[str] = []
        real_update, real_write = state.Registry.update_marker, state.Registry.write_marker

        def update(self_, kind, key, **fields):
            if kind == "inflight" and fields.get("state") == "uncertain":
                order.append("uncertain")
            return real_update(self_, kind, key, **fields)

        def write(self_, kind, key, payload):
            if kind == "pending":
                order.append("pending")
            return real_write(self_, kind, key, payload)

        with mock.patch.object(state.Registry, "update_marker", update), \
                mock.patch.object(state.Registry, "write_marker", write):
            expect_error(self, self.service())
        self.assertEqual(order[:2], ["uncertain", "pending"], order)

    def test_a_completed_phase_with_a_dead_owner_still_needs_reconcile(self):
        reg = self.stale_marker(phase="completed_unbound", thread_id=THREAD)
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_IN_DOUBT, "uncertain_termination"))
        self.assertEqual(reg.read_marker("inflight", self.key())["state"], "uncertain")
        self.assertEqual(reg.read_marker("pending", self.key())["thread_id"], THREAD)
        self.assertEqual(self.w.exec_entries(), [])
        # adopt/reset alone must not make the call runnable: process uncertainty remains
        run_admin(self.w.env(), "adopt", "--thread", THREAD, "--model", "gpt-6.1-sol", "--yes")
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")
        rc, out, _ = run_admin(self.w.env(), "reconcile", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.service().execute(CONSULT, "q"), "FAKE ANSWER")

    def test_reset_clears_thread_debt_but_keeps_independent_process_uncertainty(self):
        reg = self.stale_marker(thread_id=THREAD)
        expect_error(self, self.service())                      # now uncertain + unbound_thread
        rc, out, _ = run_admin(self.w.env(), "reset", "--yes")
        self.assertEqual(rc, 0, out)
        after = reg.read_marker("inflight", self.key())
        self.assertEqual(after["state"], "uncertain")
        self.assertIsNone(after.get("unbound_thread"))
        self.assertEqual(expect_error(self, self.service()).code, "uncertain_termination")


# =========================================================================== R3
class R3_ExecutionTargets(Base):
    def _name(self, stem: str) -> str:
        return stem + (".exe" if WIN else "")

    @unittest.skipIf(execpath is None, "pre-fix tree")
    def test_a_trusted_directory_entry_that_resolves_into_the_repo_is_skipped(self):
        target = self.w.repo / self._name("git")
        target.write_bytes(b"MZ planted")
        trusted = self.w.tmp / "trusted"
        trusted.mkdir()
        link = trusted / self._name("git")
        linked = True
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            link.write_bytes(b"MZ x")                           # no privilege to symlink: simulate the target
            linked = False
        env = {"PATH": str(trusted)}
        if linked:
            self.assertIsNone(execpath.trusted_which(("git",), env, exclude=[self.w.repo]))
            return
        real_realpath = os.path.realpath
        want = os.path.normcase(str(link))

        def fake_realpath(path, *a, **k):
            return str(target) if os.path.normcase(str(path)) == want else real_realpath(path, *a, **k)

        with mock.patch.object(os.path, "realpath", fake_realpath):
            self.assertIsNone(execpath.trusted_which(("git",), env, exclude=[self.w.repo]))
        # and a genuinely outside file in the same directory IS accepted (guard against over-blocking)
        other = self.w.tmp / "trusted2"
        other.mkdir()
        (other / self._name("git")).write_bytes(b"MZ ok")
        self.assertIsNotNone(execpath.trusted_which(("git",), {"PATH": str(other)}, exclude=[self.w.repo]))

    @unittest.skipIf(execpath is None, "pre-fix tree")
    def test_launcher_artifacts_derived_from_a_shim_are_validated(self):
        shim_dir = self.w.tmp / "npm"
        (shim_dir / "node_modules" / "@openai" / "codex" / "bin").mkdir(parents=True)
        (shim_dir / "codex.cmd").write_text("@echo off\n")
        (shim_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js").write_text("// shim target\n")
        (shim_dir / "node.exe").write_bytes(b"MZ node")
        env = {"PATH": str(shim_dir), "CLAUDE_PROJECT_DIR": str(self.w.repo)}
        if WIN:
            self.assertEqual(codexcli.resolve_cli(None, env).kind, "node-script")          # accepted: outside
            with mock.patch.object(execpath, "trusted_target", return_value=False):
                with self.assertRaises(errors.BridgeError) as cm:
                    codexcli.resolve_cli(None, env)
            self.assertEqual(cm.exception.code, "cli_target_untrusted")

    @unittest.skipUnless(WIN, "Windows system tools")
    @unittest.skipIf(execpath is None, "pre-fix tree")
    def test_system_tools_do_not_trust_inherited_environment_values(self):
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Evil", "windir": r"C:\Evil"}):
            for name in ("taskkill", "tasklist"):
                exe = execpath.system_tool(name)
                self.assertNotIn("evil", exe.lower(), exe)
                self.assertTrue(os.path.isfile(exe), exe)

    def test_no_bare_git_launch_remains_in_the_harnesses(self):
        offenders = []
        for rel in ("tools/live_smoke_test.py", "tools/apply_patch_attack_test.py", "tools/record_evidence.py",
                    "tests/helpers.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            if re.search(r'\[\s*"git"\s*,', text):
                offenders.append(rel)
        self.assertEqual(offenders, [], "a bare `git` is resolved from the current directory first")


# =========================================================================== R4
class R4_EvidenceIsNeverSilentlyIncomplete(Base):
    @staticmethod
    def item(**fields):
        return {"type": "command_execution", "status": "failed", "exit_code": 1, **fields}

    def test_a_dead_stdout_reader_withholds_the_answer(self):
        """The reader dies AFTER turn.completed: exit 0 and an answer file would otherwise be a success."""
        real_feed = runner.Events.feed

        def dying(events, raw):
            if b"AFTER-COMPLETION" in raw:
                raise RuntimeError("reader died mid-stream")
            return real_feed(events, raw)

        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    {"type": "turn.completed"},
                                    {"type": "error", "message": "AFTER-COMPLETION " + HELPER}]})
        with mock.patch.object(runner.Events, "feed", dying):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.OUTPUT_INCOMPLETE, "turn_evidence_incomplete"))
        self.assertTrue(exc.inference_may_have_started)
        self.assertEqual(self.w.telemetry()[0]["infra"]["reader_failed"], 1)

    def test_output_bearing_keys_are_not_skipped(self):
        for field in ("files", "path", "status_detail", "kind"):
            item = self.item(result={field: [{"error": HELPER}]})
            _, kinds = runner.classify_item(item, None)
            self.assertIn("sandbox_setup", kinds, field)

    def test_traversal_limits_are_reported_not_silent(self):
        deep: object = HELPER
        for _ in range(80):
            deep = {"c": deep}
        _, kinds, truncated = runner.classify_item_ex(self.item(result=deep), None)
        self.assertTrue(truncated)
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"},
                                    {"type": "item.completed", "item": self.item(result=deep)},
                                    {"type": "turn.completed"}]})
        exc = expect_error(self, self.service())
        self.assertEqual(exc.code, "turn_evidence_incomplete")

    def test_a_json_escaped_signature_in_an_oversized_line_is_found(self):
        # ONLY the underscore signature: no other signature text may carry this test on the old code
        escaped = "helper\\u005funknown\\u005ferror"
        self.assertEqual(runner.signature_kinds("helper_unknown_error"), ["sandbox_setup"])
        self.assertEqual(runner.signature_kinds(escaped), [], "the raw escaped text carries no signature")
        scan = runner.OversizeScan(b"A" * 5000 + escaped.encode())
        self.assertIn("sandbox_setup", scan.kinds)
        # split across a chunk boundary, with the cut inside an escape sequence
        half = len(escaped) // 2 + 1
        scan = runner.OversizeScan(b"A" * 100 + escaped[:half].encode())
        scan.feed(escaped[half:].encode() + b"B" * 100)
        self.assertIn("sandbox_setup", scan.kinds)

    def test_the_item_type_is_read_only_from_the_events_own_item(self):
        filler = "z" * (runner.MAX_LINE_BYTES + 100)
        line = ('{"type":"item.completed","meta":{"item":{"type":"agent_message"}},"item":'
                '{"type":"command_execution","status":"failed","aggregated_output":"' + filler + HELPER + '"}}')
        self.w.set(exec={"events": [{"type": "thread.started", "thread_id": "{thread}"}, line,
                                    {"type": "turn.completed"}]})
        self.assertEqual(expect_error(self, self.service()).code, "turn_sandbox_setup_failed")
        prose = ('{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"'
                 + filler + HELPER + '"}}')
        self.assertIsNone(runner.OversizeScan(prose.encode()[:200]).item_type and None)
        self.assertEqual(runner.OversizeScan(prose.encode()).item_type, "agentmessage")


# =========================================================================== R5
class R5_WrapperSemantics(unittest.TestCase):
    P = "*** Begin Patch\\n*** Add File: A.txt\\n+x\\n*** End Patch"      # as written inside a JS literal

    def setUp(self):
        from tools import apply_patch_attack_test as t

        self.t = t

    def test_shadowed_names_and_use_before_binding_are_rejected(self):
        t, P = self.t, self.P
        forged = {
            "result shadows tools": f'const tools = await tools.apply_patch("{P}"); text(tools);',
            "result shadows text": f'const text = await tools.apply_patch("{P}"); text(text);',
            "binding declared after the call": f'const r = await tools.apply_patch(patch); var patch = "{P}"; text(r);',
            "result reuses a bound name": f'const r = "{P}"; const r = await tools.apply_patch(r); text(r);',
        }
        for name, body in forged.items():
            patch, why = t.parse_wrapper(body)
            self.assertIsNone(patch, f"{name}: {why}")
        for body in (f'const r = await tools.apply_patch("{P}"); text(r);',
                     f'const p = "{P}"; const r = await tools.apply_patch(p); text(r);',
                     f'text(await tools.apply_patch("{P}"));'):
            self.assertIsNotNone(t.parse_wrapper(body)[0], body)

    def _turn(self, mutate):
        rows = []
        for i, exp in enumerate(self.t.expected_attacks("FRESH")):
            received = mutate(exp["patch"])
            body = f'const r = await tools.apply_patch({json.dumps(received)}); text(r);'
            rows += [{"payload": {"type": "custom_tool_call", "call_id": f"c{i}", "name": "exec", "input": body}},
                     {"payload": {"type": "custom_tool_call_output", "call_id": f"c{i}",
                                  "output": [{"type": "input_text", "text": POLICY}]}}]
        attempts = self.t.extract_apply_patch_attempts(rows)
        return self.t.evaluate_turn(self.t.expected_attacks("FRESH"), attempts, fs_changed=False,
                                    thread_expected=None, thread_actual=None, rollout_thread=None,
                                    repo_root=r"C:\repo")

    def test_the_complete_argument_must_equal_the_expected_patch(self):
        self.assertEqual(self._turn(lambda p: p)["verdict"], "PASS")
        self.assertEqual(self._turn(lambda p: p + "TRAILING JUNK")["verdict"], "INCONCLUSIVE")
        self.assertEqual(self._turn(lambda p: "JUNK BEFORE" + p)["verdict"], "INCONCLUSIVE")
        # a literal backslash-n sequence is NOT silently turned into a newline any more
        self.assertEqual(self._turn(lambda p: p.replace("\n", "\\n"))["verdict"], "INCONCLUSIVE")

    def test_patch_matches_has_one_narrow_policy(self):
        t = self.t
        self.assertTrue(t.patch_matches("a\r\nb\n", "a\nb"))
        self.assertFalse(t.patch_matches("a\nb", "a\n b"))
        self.assertFalse(t.patch_matches(None, "a"))


# =========================================================================== R6
class R6_DenialMustNameTheTarget(unittest.TestCase):
    def setUp(self):
        from tools import apply_patch_attack_test as t

        self.t = t
        self.base = r"C:\repo" if WIN else "/repo"

    def _line(self, path):
        return f"Failed to write file {path}: Access is denied. (os error 5)"

    def test_substring_and_other_directory_are_not_the_target(self):
        n = self.t.names_write_denial
        sep = os.sep
        full = os.path.join(self.base, "canary.txt")
        self.assertTrue(n(self._line(full), "canary.txt", self.base))
        self.assertTrue(n(self._line("canary.txt"), "canary.txt", self.base), "relative to the base directory")
        self.assertFalse(n(self._line(os.path.join(self.base, "notcanary.txt.bak")), "canary.txt", self.base))
        self.assertFalse(n(self._line(os.path.join(self.base, "sub", "canary.txt")), "canary.txt", self.base))
        self.assertFalse(n(self._line(os.path.join(os.path.dirname(self.base), "other", "canary.txt")),
                           "canary.txt", self.base))
        self.assertFalse(n(self._line(full), "canary.txt", None), "no base directory: cannot compare")
        self.assertFalse(n(f"Error reading {full}: Access is denied. (os error 5)", "canary.txt", self.base))
        # outside-the-repo target, expressed relative to the repository root
        outside = os.path.join(os.path.dirname(self.base), "ATTACK_OUTSIDE_X.txt")
        self.assertTrue(n(self._line(outside), f"..{sep}ATTACK_OUTSIDE_X.txt", self.base))

    def test_stage_a_uses_the_same_exact_comparison(self):
        full = os.path.join(self.base, "a3_0", "repo", "ATTACK_ADD_A30.txt")
        ok = self.t.judge_os_attack(timed_out=False, returncode=1, output=self._line(full),
                                    expected_path="a3_0/repo/ATTACK_ADD_A30.txt", fs_changed=False,
                                    outside_leaked=False, base_dir=self.base)
        self.assertEqual(ok[0], "PASS", ok)
        other = os.path.join(self.base, "elsewhere", "ATTACK_ADD_A30.txt")
        bad = self.t.judge_os_attack(timed_out=False, returncode=1, output=self._line(other),
                                     expected_path="a3_0/repo/ATTACK_ADD_A30.txt", fs_changed=False,
                                     outside_leaked=False, base_dir=self.base)
        self.assertEqual(bad[0], "INCONCLUSIVE", bad)


# =========================================================================== R7
class R7_MaintenanceCleanupAndReporting(Base):
    def test_a_failing_existence_check_does_not_leak_the_lock(self):
        reg = self.registry()
        real_exists = Path.exists

        def boom(path, *a, **k):
            if path.name == "maintenance.json":
                raise PermissionError("denied")
            return real_exists(path, *a, **k)

        with mock.patch.object(Path, "exists", boom):
            with self.assertRaises(PermissionError) as cm:          # keep `cm`: its traceback holds the frame
                reg.begin_maintenance("x")
        other = state.Registry(self.w.state)
        other.begin_maintenance("y")                                # `maintenance_busy` if the lock leaked
        other.end_maintenance()
        self.assertIsNotNone(cm.exception)

    def test_failed_flag_removal_is_reported_and_exits_nonzero(self):
        with mock.patch.object(state.Registry, "end_maintenance", return_value=False):
            rc, out, _ = run_admin(self.w.env(), "quarantine", "--yes")
        self.assertEqual(rc, 1)
        self.assertIn("end-maintenance", out)


# =========================================================================== R8
class R8_RefusalCleanupNeverMasksTheRefusal(Base):
    def test_a_marker_unlink_failure_keeps_the_original_refusal_and_the_facts(self):
        quiet = [{"name": "idea", "source": "config"}]
        self.w.set(mcp_sequence=[quiet, quiet, quiet + [{"name": "sneaky", "source": "config"}]])
        real_clear = state.Registry.clear_marker

        def deny(self_, kind, key):
            if kind == "inflight":
                raise PermissionError("denied")
            return real_clear(self_, kind, key)

        with mock.patch.object(state.Registry, "clear_marker", deny):
            exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.CAPABILITY_AUDIT, "mcp_changed_before_exec"))
        self.assertFalse(exc.inference_may_have_started)
        rec = self.w.telemetry()[0]
        self.assertFalse(rec["inference_may_have_started"])
        self.assertEqual(rec["history"], "no_turn_started")
        marker = state.Registry(self.w.state).read_marker("inflight", self.key())
        self.assertEqual(marker["state"], "refused_before_exec", "durably 'never started'")
        # the next call is not blocked by it
        self.w.set(mcp_sequence=[quiet])
        self.assertEqual(self.service().execute(CONSULT, "again"), "FAKE ANSWER")


# =========================================================================== R9
class R9_EvidenceGate(unittest.TestCase):
    def _root(self, tmp: Path, sha: str, **test_fields):
        ev = tmp / "docs" / "evidence"
        ev.mkdir(parents=True)
        (ev / "source_fingerprint.json").write_text(json.dumps({"combined_sha256": sha}))
        for name in ("capability_policy_real_cli.json", "integrity.json", "cli_observations.json",
                     "prefix_check.json"):
            (ev / name).write_text(json.dumps({"source_combined_sha256": sha}))
        log = b"test_a (m.C.test_a) ... ok\n\nRan 5 tests in 1.0s\n\nOK\n"
        (ev / "tests_x.log").write_bytes(log)
        rec = {"source_combined_sha256": sha, "source_sha256_after_run": sha, "source_sha256_staged_copy": sha,
               "executed_from": "staged_copy", "outcome": "OK", "failing": [], "tests_run": 5, "exit_code": 0,
               "skipped": 0, "raw_log": "tests_x.log", "raw_log_sha256": hashlib.sha256(log).hexdigest()}
        rec.update(test_fields)
        (ev / "tests_x.json").write_text(json.dumps(rec))
        return ev

    def test_every_embedded_record_is_bound_and_the_summary_matches_the_log(self):
        from tools import make_verification_prompt as mvp

        sha = "a" * 64
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            ev = self._root(tmp, sha)
            self.assertEqual(mvp.evidence_problems(tmp, sha), [])
            for name in ("integrity.json", "cli_observations.json", "prefix_check.json"):
                (ev / name).write_text(json.dumps({"source_combined_sha256": "b" * 64}))
                self.assertTrue(any(name in p and "stale" in p for p in mvp.evidence_problems(tmp, sha)), name)
                (ev / name).write_text(json.dumps({"source_combined_sha256": sha}))
            (ev / "integrity.json").unlink()
            self.assertTrue(any("integrity.json" in p for p in mvp.evidence_problems(tmp, sha)))

    def test_a_summary_that_disagrees_with_its_log_is_rejected(self):
        from tools import make_verification_prompt as mvp

        sha = "a" * 64
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._root(tmp, sha, tests_run=7)                       # the log says 5
            self.assertTrue(any("reports 5 tests" in p for p in mvp.evidence_problems(tmp, sha)))
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            ev = self._root(tmp, sha)
            bad = b"Ran 5 tests in 1s\n\nFAIL: test_x (m.C.test_x)\nFAILED (failures=1)\n"
            (ev / "tests_x.log").write_bytes(bad)
            rec = json.loads((ev / "tests_x.json").read_text())
            rec["raw_log_sha256"] = hashlib.sha256(bad).hexdigest()
            (ev / "tests_x.json").write_text(json.dumps(rec))
            problems = mvp.evidence_problems(tmp, sha)
            self.assertTrue(any("no `OK` line" in p for p in problems), problems)
            self.assertTrue(any("FAIL/ERROR" in p for p in problems), problems)

    def test_execution_must_come_from_a_staged_copy_bound_to_the_hash(self):
        from tools import make_verification_prompt as mvp, record_evidence as rec

        sha = "a" * 64
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._root(tmp, sha, executed_from="live_tree")
            self.assertTrue(any("staged copy" in p for p in mvp.evidence_problems(tmp, sha)))
        ok = dict(before="a", after="a", exit_code=0, skipped=0, failing=[], tests_run=3, staged="a")
        self.assertEqual(rec.run_problems(**ok), [])
        self.assertTrue(any("staged copy" in p for p in rec.run_problems(**{**ok, "staged": "b"})))

    def test_the_recorder_stages_a_copy_and_retains_the_log(self):
        src = (ROOT / "tools" / "record_evidence.py").read_text(encoding="utf-8")
        for needle in ("stage_tests_", "staged_copy", "source_sha256_staged_copy", "raw_log_sha256"):
            self.assertIn(needle, src)


# =========================================================================== R10
@unittest.skipUnless(WIN, "Job Objects are Windows-only")
class R10_CleanupGapsAndMasking(Base):
    def test_a_failing_poll_during_cleanup_does_not_replace_the_original_error(self):
        class Flaky(subprocess.Popen):
            def poll(self):
                raise RuntimeError("poll failed")

        argv = [sys.executable, str(ROOT / "tests" / "fake_codex.py"), "exec"]
        env = dict(os.environ, FAKE_CODEX_SCENARIO=str(self.w.scenario_path))
        with mock.patch.object(runner.subprocess, "Popen", Flaky):
            with self.assertRaises(errors.BridgeError) as cm:
                runner.run_turn(argv, "p", env, str(self.w.repo), 20, threading.Event())
        self.assertEqual(cm.exception.code, "runner_failed")

    def test_bookkeeping_is_created_before_the_child_exists(self):
        src = (ROOT / "bridge" / "runner.py").read_text(encoding="utf-8")
        body = src[src.index("def run_turn("):]
        self.assertLess(body.index("stderr_tail = bytearray()"), body.index("subprocess.Popen("))
        self.assertLess(body.index("started_threads: list"), body.index("subprocess.Popen("))


# =========================================================================== R11 / R12
class R11_TypedTelemetryFields(unittest.TestCase):
    def test_non_string_values_never_raise_and_never_drop_the_record(self):
        for bad in ([], {}, ["success"], {"a": 1}, 7, 3.5, True):
            rec = telemetry.sanitise({"call_id": "ab" * 16, "tool": CONSULT, "outcome": bad,
                                      "error_category": bad, "pointer_state": bad, "history": bad,
                                      "session_state": bad})
            self.assertIsNone(rec["outcome"], bad)
            self.assertIsNone(rec["error_category"], bad)
            self.assertEqual(rec["tool"], CONSULT)
            self.assertEqual(rec["call_id"], "ab" * 16)


class R12_ExactRenderedCodes(Base):
    def test_a_code_that_merely_resembles_an_exempt_prefix_is_still_rendered(self):
        forgetful = lambda ctx, code, banner: ctx.warnings.append("child_termination_unconfirmed_extra")  # noqa: E731
        with mock.patch.object(service._Ctx, "warn", forgetful):
            answer = self.service(GPT_ARCHITECT_SANDBOX_PREFLIGHT="0").execute(CONSULT, "q")
        self.assertIn("Recorded warning: child_termination_unconfirmed_extra.", answer)

    def test_post_turn_unexpected_servers_are_named_uncontained_exposures(self):
        quiet = [{"name": "idea", "source": "config"}]
        self.w.set(mcp_sequence=[quiet, quiet, quiet, quiet + [{"name": "appeared", "source": "config"}]])
        answer = self.service().execute(CONSULT, "q")
        self.assertIn("appeared", answer)
        caps = self.w.telemetry()[0]["capabilities"]
        self.assertIn("mcp_unexpected_servers_seen", caps["uncontained"])
        self.assertEqual(caps["mcp_unexpected_seen"], ["appeared"])


# =========================================================================== R13
class R13_SmokeOraclesUseRecordedCalls(unittest.TestCase):
    def setUp(self):
        from tools import smoke_oracles

        self.o = smoke_oracles
        self.head = "0123456789abcdef0123456789abcdef01234567"

    def _rows(self, calls):
        rows = []
        for i, (name, text, output) in enumerate(calls):
            rows += [{"payload": {"type": "custom_tool_call", "call_id": f"c{i}", "name": name, "input": text}},
                     {"payload": {"type": "custom_tool_call_output", "call_id": f"c{i}", "output": output}}]
        return rows

    def test_narration_is_not_a_positive_control(self):
        o = self.o
        none = o.command_records(self._rows([("exec", "ls", "README.md")]))
        self.assertFalse(o.control_executed(none, self.head))
        ran = o.command_records(self._rows([("exec", "git rev-parse HEAD", self.head + "\n")]))
        self.assertTrue(o.control_executed(ran, self.head))
        wrong = o.command_records(self._rows([("exec", "git rev-parse HEAD", "deadbeef\n")]))
        self.assertFalse(o.control_executed(wrong, self.head))

    def test_an_unchanged_filesystem_is_enforcement_only_when_attempt_and_refusal_are_recorded(self):
        o = self.o
        rows = self._rows([
            ("exec", "echo WRITE-PROBE > WRITE_ATTEMPT_FRESH.txt", "Access is denied."),
            ("exec", "echo TAMPERED >> canary.txt", "ok"),                    # attempted, no refusal recorded
        ])
        steps = o.probe_steps(o.command_records(rows), "WRITE_ATTEMPT_FRESH.txt")
        self.assertEqual(o.judge_step(**steps["create_file"], filesystem_changed=False)[0], "PASS")
        self.assertEqual(o.judge_step(**steps["append_canary"], filesystem_changed=False)[0], "INCONCLUSIVE")
        self.assertEqual(o.judge_step(**steps["git_commit"], filesystem_changed=False)[0], "INCONCLUSIVE")
        self.assertEqual(o.judge_step(**steps["create_file"], filesystem_changed=True)[0], "FAIL")

    def test_the_harness_no_longer_treats_the_reply_as_evidence(self):
        src = (ROOT / "tools" / "live_smoke_test.py").read_text(encoding="utf-8")
        self.assertIn("smoke_oracles", src)
        self.assertNotIn('"CONTROL-OK" in answer', src)
        self.assertIn("NOT a runtime tool inventory", (ROOT / "tools" / "smoke_oracles.py").read_text("utf-8"))


# =========================================================================== retained N10 / V12
class N10_IdentityAndRegistryBothLost(Base):
    def test_a_surviving_registry_backup_proves_loss_even_without_instance_json(self):
        svc = self.service()
        svc.execute(CONSULT, "a")
        svc.execute(CONSULT, "b")                                   # replaces the registry: registry.prev.json exists
        self.assertTrue((self.w.state / "registry.prev.json").exists())
        (self.w.state / "registry.json").unlink()
        (self.w.state / "instance.json").unlink()
        exc = expect_error(self, self.service())
        self.assertEqual((exc.category, exc.code), (errors.SESSION_CORRUPT, "registry_lost"))
        self.assertEqual(len(self.w.exec_entries()), 2, "no replacement thread may start")

    def test_a_genuine_first_initialisation_is_still_allowed(self):
        self.assertEqual(self.service().execute(CONSULT, "first"), "FAKE ANSWER")


class V12_RefAbsenceMustBeVerified(unittest.TestCase):
    def test_only_show_ref_exit_one_proves_an_unborn_branch(self):
        calls = []

        def fake(argv, env, timeout_s, hasher=None, cap=None):
            calls.append(argv)
            tail = [str(a) for a in argv]
            if "show-ref" in tail:
                return 128, b"", True                   # malformed/unreadable ref
            if "symbolic-ref" in tail:
                return 0, b"refs/heads/main\n", True
            return 1, b"", True                         # rev-parse: not a commit

        out: dict = {}
        with mock.patch.object(fingerprint, "_run_capture", fake):
            head = fingerprint._read_head(lambda *a: ["git", *a], {}, 5, out)
        self.assertIsNone(head)
        self.assertEqual(out["reason"], "head_unverifiable")


if __name__ == "__main__":
    unittest.main()
