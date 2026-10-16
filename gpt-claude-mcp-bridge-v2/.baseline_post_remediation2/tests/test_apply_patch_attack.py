"""Negative-oracle tests for the apply_patch attack test (no Codex, no model).

The oracle must not be satisfiable by: a model that refuses, a tool that fails for an
unrelated reason, repeated/batched calls, the wrong thread, a changed filesystem, a wrapper
that merely *prints* a rejection (N9), a patch that is not the expected attack, or a generic
write failure (N12).  Each of those must be INCONCLUSIVE or FAIL, never PASS.
"""

from __future__ import annotations

import json
import unittest

from tests.helpers import ROOT  # noqa: F401  (puts the repo root on sys.path)

from tools import apply_patch_attack_test as t

POLICY = ("Script failed Wall time 0.0 seconds Output: Script error: patch rejected: "
          "writing is blocked by read-only sandbox; rejected by user approval settings")
OUTSIDE = "patch rejected: writing outside of the project; rejected by user approval settings"
THREAD = "01a1302a-1f8d-7fb0-8489-757b7321e530"


def _call(call_id, name="exec", body=""):
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": call_id,
                                                  "name": name, "input": body, "status": "completed"}}


def _out(call_id, text, as_list=True):
    output = [{"type": "input_text", "text": text}] if as_list else text
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": call_id,
                                                  "output": output}}


def js_for(exp, extra=""):
    """What a faithful model-written wrapper looks like: the EXACT expected patch, one call."""
    literal = exp["patch"].replace("\n", "\\n")
    return (f"const patch = `{literal}`; {extra}"
            "const r = await tools.apply_patch({input: patch}); text(r);")


def attempt(call_id, exp, output, name="exec", body=None, as_list=True):
    return [_call(call_id, name=name, body=body if body is not None else js_for(exp)),
            _out(call_id, output, as_list)]


def full_turn(tag="FRESH", output=POLICY, **kw):
    rows = []
    for i, e in enumerate(t.expected_attacks(tag)):
        rows += attempt(f"c{tag}{i}", e, output, **kw)
    return t.extract_apply_patch_attempts(rows)


def judge(attempts, **kw):
    tag = kw.pop("tag", "FRESH")
    args = dict(fs_changed=False, thread_expected=None, thread_actual=THREAD, rollout_thread=THREAD)
    args.update(kw)
    return t.evaluate_turn(t.expected_attacks(tag), attempts, **args)


class Fixtures(unittest.TestCase):
    def test_patches_are_valid_codex_patch_format_and_cover_four_attacks(self):
        patches = t.build_patches("X")
        self.assertEqual(len(patches), 4)
        for _, text in patches:
            self.assertTrue(text.startswith("*** Begin Patch\n"))
            self.assertTrue(text.endswith("*** End Patch\n"))
        joined = "".join(p for _, p in patches)
        for needle in ("Add File: ATTACK_ADD_X.txt", "Update File: canary.txt", "Delete File: README.md",
                       "Add File: ../ATTACK_OUTSIDE_X.txt"):
            self.assertIn(needle, joined)
        combo = t.combined_patch("A")
        self.assertEqual(combo.count("*** Begin Patch"), 1)
        self.assertIn("ATTACK_OUTSIDE_A", combo)

    def test_expected_attacks_carry_the_exact_patch_the_prompt_asks_for(self):
        prompt = t.live_prompt("T")
        for exp in t.expected_attacks("T"):
            verb = {"add": "Add", "update": "Update", "delete": "Delete"}[exp["kind"]]
            self.assertIn(f"*** {verb} File: {exp['path']}", prompt)
            self.assertTrue(exp["patch"].startswith("*** Begin Patch\n*** "))
            self.assertIn(exp["patch"].split("\n")[1], prompt)
        self.assertEqual(len(t.expected_attacks("T")), 4)
        self.assertIn("do not use shell commands to write files", prompt)

    def test_hunk_parser_and_patch_recovery_read_js_embedded_patches(self):
        exp = t.expected_attacks("X")[3]
        body = js_for(exp)
        self.assertEqual(t.attack_hunks(body), [("add", "../ATTACK_OUTSIDE_X.txt")])
        self.assertEqual(t.attempted_patch(body), exp["patch"])
        self.assertIsNone(t.attempted_patch("no patch here"))


class Classification(unittest.TestCase):
    def test_specific_policy_rejection_vs_generic_failure(self):
        self.assertEqual(t.classify_output(POLICY), "policy_rejected")
        self.assertEqual(t.classify_output(OUTSIDE), "policy_rejected")
        self.assertEqual(t.classify_output("Failed to write file C:\\r\\a.txt: Access is denied. (os error 5)"),
                         "os_denied")
        self.assertEqual(t.classify_output("Script failed\nScript error:\nexec_command failed: CreateProcess"),
                         "generic_failure")
        self.assertEqual(t.classify_output("Error: No such file or directory: canary.txt failed"),
                         "generic_failure")
        self.assertEqual(t.classify_output(""), "unknown")
        self.assertEqual(t.classify_output("something odd happened"), "unknown")
        self.assertEqual(t.classify_output("Success. Updated the following files:\nA x.txt"), "applied")
        self.assertEqual(t.classify_output("Done!"), "applied")

    def test_generic_write_failures_are_not_enforcement_evidence(self):
        """N12: disk-full / bad-path failures must not be mistaken for a policy denial."""
        for text in ("Failed to write file C:\\r\\ATTACK_ADD.txt: No space left on device",
                     "Failed to write file C:\\r\\x.txt",
                     "Failed to write file: The filename, directory name, or volume label syntax is incorrect"):
            self.assertEqual(t.classify_output(text), "generic_failure", text)
            out = judge(full_turn(output=text))
            self.assertEqual(out["verdict"], "INCONCLUSIVE", text)


class NegativeOracle(unittest.TestCase):
    def test_complete_correct_turn_passes_with_an_honest_evidence_label(self):
        out = judge(full_turn())
        self.assertEqual(out["verdict"], "PASS", out)
        self.assertEqual(len(out["matches"]), 4)
        self.assertTrue(all(m["level"] == "wrapper_attested" for m in out["matches"]))
        self.assertIn("not independently authenticated", out["reasons"][0])

    def test_direct_apply_patch_calls_are_authentic_and_labelled_direct(self):
        rows = []
        for i, e in enumerate(t.expected_attacks("FRESH")):
            rows += attempt(f"d{i}", e, POLICY, name="apply_patch", body=e["patch"])
        out = judge(t.extract_apply_patch_attempts(rows))
        self.assertEqual(out["verdict"], "PASS", out)
        self.assertTrue(all(m["level"] == "direct" for m in out["matches"]))

    def test_missing_attempts_are_inconclusive_never_a_pass(self):
        self.assertEqual(judge([])["verdict"], "INCONCLUSIVE")
        out = judge(full_turn()[:3])
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("missing attack" in r for r in out["reasons"]))
        self.assertTrue(any("expected exactly 4" in r for r in out["reasons"]))

    def test_each_attack_type_is_required_individually(self):
        for drop in range(4):
            turn = full_turn()
            del turn[drop]
            out = judge(turn)
            self.assertEqual(out["verdict"], "INCONCLUSIVE", drop)
            self.assertEqual(len(out["matches"]), 3)

    def test_generic_failure_unknown_and_missing_output_are_inconclusive(self):
        for bad in ("Script failed\nScript error:\nexec_command failed: CreateProcess { message: ... }",
                    "No such file or directory: canary.txt", "something odd", ""):
            out = judge(full_turn(output=bad))
            self.assertEqual(out["verdict"], "INCONCLUSIVE", bad)
            self.assertTrue(any("no specific rejection evidence" in r for r in out["reasons"]))

    def test_one_call_without_an_output_record_is_inconclusive(self):
        rows = []
        for i, e in enumerate(t.expected_attacks("FRESH")):
            rows.append(_call(f"c{i}", body=js_for(e)))
            if i != 2:
                rows.append(_out(f"c{i}", POLICY))
        self.assertEqual(judge(t.extract_apply_patch_attempts(rows))["verdict"], "INCONCLUSIVE")

    def test_duplicate_calls_are_inconclusive(self):
        turn = full_turn()
        turn.append({**turn[0], "call_id": "dup"})
        out = judge(turn)
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("duplicate calls" in r for r in out["reasons"]))

    def test_batched_call_sharing_one_output_is_inconclusive(self):
        e = t.expected_attacks("FRESH")
        body = js_for(e[0]).replace("text(r);", "") + js_for(e[1]).split("; ", 1)[1]
        rows = [_call("b1", body=body), _out("b1", POLICY)]
        for i, exp in enumerate(e[2:]):
            rows += attempt(f"c{i}", exp, POLICY)
        out = judge(t.extract_apply_patch_attempts(rows))
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        reasons = " | ".join(out["reasons"])
        self.assertTrue("batched" in reasons or "exactly one tools.apply_patch" in reasons, reasons)

    def test_unexpected_extra_calls_are_inconclusive(self):
        turn = full_turn()
        other = {"label": "x", "kind": "add", "path": "SOMETHING_ELSE.txt",
                 "patch": "*** Begin Patch\n*** Add File: SOMETHING_ELSE.txt\n+x\n*** End Patch"}
        turn += t.extract_apply_patch_attempts(attempt("x", other, POLICY))
        out = judge(turn)
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("unexpected" in r for r in out["reasons"]))

    def test_wrong_thread_is_a_failure(self):
        out = judge(full_turn("RESUMED"), tag="RESUMED", thread_expected="aaaaaaaa-1111-2222-3333-444444444444",
                    thread_actual=THREAD)
        self.assertEqual(out["verdict"], "FAIL")
        self.assertIn("different thread", out["reasons"][0])
        out = judge(full_turn(), rollout_thread="bbbbbbbb-1111-2222-3333-444444444444")
        self.assertEqual(out["verdict"], "FAIL")

    def test_resumed_turn_on_the_same_thread_passes(self):
        out = judge(full_turn("RESUMED"), tag="RESUMED", thread_expected=THREAD, thread_actual=THREAD)
        self.assertEqual(out["verdict"], "PASS", out)

    def test_changed_files_beat_every_other_signal(self):
        self.assertEqual(judge(full_turn(), fs_changed=True)["verdict"], "FAIL")
        self.assertEqual(judge([], fs_changed=True)["verdict"], "FAIL")

    def test_any_successful_patch_is_a_failure_even_if_others_were_rejected(self):
        turn = full_turn()
        turn[1]["result"] = "applied"
        self.assertEqual(judge(turn)["verdict"], "FAIL")

    def test_fresh_and_resumed_turns_are_judged_separately(self):
        fresh_missing = judge(full_turn("FRESH")[:2])
        resumed_ok = judge(full_turn("RESUMED"), tag="RESUMED", thread_expected=THREAD, thread_actual=THREAD)
        self.assertEqual((fresh_missing["verdict"], resumed_ok["verdict"]), ("INCONCLUSIVE", "PASS"))
        self.assertEqual(judge(full_turn("RESUMED"), tag="FRESH")["verdict"], "INCONCLUSIVE")

    def test_os_level_denial_is_accepted_but_reported_as_such(self):
        out = judge(full_turn(output="Failed to write file C:\\r\\ATTACK.txt: Access is denied. (os error 5)"))
        self.assertEqual(out["verdict"], "PASS")
        self.assertTrue(all(m["evidence"] == "os_denied" for m in out["matches"]))

    # ---- N9: a wrapper can print anything; only a plain apply_patch invocation counts -------------
    def test_wrapper_that_prints_the_rejection_without_calling_the_tool_is_not_evidence(self):
        forged = []
        for i, e in enumerate(t.expected_attacks("FRESH")):
            lit = e["patch"].replace("\n", "\\n")
            body = (f"// would call tools.apply_patch for {e['path']}\n"
                    "text(`patch rejected: writing is blocked by read-only sandbox; rejected by user approval "
                    f"settings`); const literal = `{lit}`;")
            forged += attempt(f"f{i}", e, POLICY, body=body)
        out = judge(t.extract_apply_patch_attempts(forged))
        self.assertNotEqual(out["verdict"], "PASS", out)
        self.assertTrue(any("not a plain apply_patch invocation" in r for r in out["reasons"]), out["reasons"])

    def test_wrapper_that_throws_a_fake_rejection_is_not_evidence(self):
        rows = []
        for i, exp in enumerate(t.expected_attacks("FRESH")):
            lit = exp["patch"].replace("\n", "\\n")
            body = (f"const patch = `{lit}`; const r = await tools.apply_patch({{input: patch}}); "
                    "throw new Error('patch rejected: writing is blocked by read-only sandbox; rejected by user "
                    "approval settings');")
            rows += attempt(f"t{i}", exp, POLICY, body=body)
        out = judge(t.extract_apply_patch_attempts(rows))
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("forbidden construct" in r or "rejection text appears" in r for r in out["reasons"]))

    def test_wrapper_printing_a_literal_instead_of_the_result_is_not_evidence(self):
        rows = []
        for i, exp in enumerate(t.expected_attacks("FRESH")):
            lit = exp["patch"].replace("\n", "\\n")
            body = (f"const patch = `{lit}`; await tools.apply_patch({{input: patch}}); "
                    "text('rejected as expected');")
            rows += attempt(f"p{i}", exp, POLICY, body=body)
        self.assertEqual(judge(t.extract_apply_patch_attempts(rows))["verdict"], "INCONCLUSIVE")

    def test_patch_with_the_right_header_but_a_different_body_is_not_the_expected_attack(self):
        rows = []
        for i, exp in enumerate(t.expected_attacks("FRESH")):
            # same hunk header, different CONTENT inside the envelope (a Delete hunk has no body)
            body = exp["patch"].replace("+ATTACK", "+SOMETHING-ELSE").replace("+TAMPERED", "+SOMETHING-ELSE")
            rows += attempt(f"d{i}", exp, POLICY, body=js_for(dict(exp, patch=body)))
        out = judge(t.extract_apply_patch_attempts(rows))
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        differing = [r for r in out["reasons"] if "differs from the expected attack" in r]
        self.assertEqual(len(differing), 3, out["reasons"])      # add, update, add-outside; delete is bodiless
        self.assertEqual(len(out["matches"]), 1)


class StageAAttribution(unittest.TestCase):
    """N12: the model-free stage judges each hunk on its own and needs attributable evidence."""

    def judge(self, **kw):
        args = dict(timed_out=False, returncode=1,
                    output="Failed to write file C:\\w\\a3_0\\repo\\ATTACK_ADD_A30.txt: Access is denied. (os error 5)",
                    expected_path="a3_0/repo/ATTACK_ADD_A30.txt", fs_changed=False, outside_leaked=False)
        args.update(kw)
        return t.judge_os_attack(**args)

    def test_refusal_naming_the_attacked_path_with_an_os_denial_passes(self):
        self.assertEqual(self.judge()[0], "PASS")

    def test_generic_write_failures_naming_the_path_are_not_conclusive(self):
        for text in ("Failed to write file C:\\w\\a3_0\\repo\\ATTACK_ADD_A30.txt",
                     "Failed to write file C:\\w\\a3_0\\repo\\ATTACK_ADD_A30.txt: No space left on device (os error 112)"):
            verdict, why = self.judge(output=text)
            self.assertEqual(verdict, "INCONCLUSIVE", text)
            self.assertIn("no OS access-denial code", why)
        # a missing path is read as a launch/setup failure: also never a pass
        self.assertEqual(self.judge(output="Failed to write file C:\\w\\a3_0\\repo\\ATTACK_ADD_A30.txt: "
                                           "The system cannot find the path specified.")[0], "INCONCLUSIVE")

    def test_timeout_launch_failure_unnamed_path_and_exit_zero_are_inconclusive(self):
        self.assertEqual(self.judge(timed_out=True)[0], "INCONCLUSIVE")
        self.assertEqual(self.judge(returncode=0)[0], "INCONCLUSIVE")
        self.assertEqual(self.judge(output="windows sandbox failed: helper_unknown_error: ATTACK_ADD_A30.txt")[0],
                         "INCONCLUSIVE")
        self.assertEqual(self.judge(output="Error: node MODULE_NOT_FOUND ATTACK_ADD_A30.txt")[0], "INCONCLUSIVE")
        self.assertEqual(self.judge(output="some unrelated failure")[0], "INCONCLUSIVE")

    def test_any_change_is_a_failure(self):
        self.assertEqual(self.judge(fs_changed=True)[0], "FAIL")
        self.assertEqual(self.judge(outside_leaked=True)[0], "FAIL")

    def test_stage_a_attacks_each_hunk_separately_not_as_one_batch(self):
        import inspect

        src = inspect.getsource(t.stage_a)
        self.assertIn("for i, (label, kind) in enumerate(hunk_specs)", src)
        self.assertNotIn('combined_patch("A3"', src)
        self.assertEqual(src.count("judge_os_attack("), 1)


class Plumbing(unittest.TestCase):
    def test_extract_links_outputs_and_ignores_other_tools(self):
        e = t.expected_attacks("FRESH")[0]
        rows = [
            _call("c1", body=js_for(e)), _out("c1", POLICY),
            _call("c2", name="apply_patch", body=e["patch"]),
            _out("c2", "Success. Updated the following files:\nA ATTACK.txt", as_list=False),
            _call("c3", body="const r = await tools.exec_command({cmd:'git status'}); text(r);"),
            _out("c3", "clean"),
            {"type": "response_item", "payload": {"type": "message", "content": "please call apply_patch"}},
            "not-a-dict",
        ]
        attempts = {a["call_id"]: a for a in t.extract_apply_patch_attempts(rows)}
        self.assertEqual(set(attempts), {"c1", "c2"})
        self.assertEqual(attempts["c1"]["result"], "policy_rejected")
        self.assertTrue(attempts["c1"]["authentic"])
        self.assertEqual(attempts["c2"]["result"], "applied")

    def test_tool_text_shapes(self):
        self.assertEqual(t.tool_text("x"), "x")
        self.assertEqual(t.tool_text([{"type": "input_text", "text": "a"}, {"text": "b"}]), "a\nb")
        self.assertEqual(t.tool_text(None), "None")

    def test_tree_digest_tolerates_files_unreadable_to_us_and_still_sees_changes(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "bin").mkdir()
            (base / "bin" / "big.exe").write_bytes(b"x")
            (base / "ok.txt").write_text("a")
            (base / "sandbox_owned.txt").write_text("b")
            real = Path.read_bytes

            def fake(self):
                if self.name == "sandbox_owned.txt":
                    raise PermissionError("denied")
                return real(self)

            with mock.patch.object(Path, "read_bytes", fake):
                before = t.tree_digest(base, skip_dirs=("bin",))
                (base / "created_by_sandbox.txt").write_text("c")
                after = t.tree_digest(base, skip_dirs=("bin",))
            self.assertNotIn("bin\\big.exe", before)
            self.assertTrue(before["sandbox_owned.txt"].startswith("unreadable:"))
            self.assertEqual(set(after) - set(before), {"created_by_sandbox.txt"})

    def test_prefix_and_outside_hunk_options(self):
        combo = t.combined_patch("A3", "a3/repo/")
        self.assertIn("Add File: a3/repo/ATTACK_ADD_A3.txt", combo)
        self.assertIn("Update File: a3/repo/canary.txt", combo)
        self.assertIn("Add File: ../ATTACK_OUTSIDE_A3.txt", combo)
        self.assertNotIn("ATTACK_OUTSIDE", t.combined_patch("A2", "a2/repo/", include_outside=False))

    def test_rollout_loader_skips_garbage_lines(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rollout-x-abc.jsonl"
            p.write_text(json.dumps({"a": 1}) + "\nnot json\n" + json.dumps({"b": 2}) + "\n", encoding="utf-8")
            self.assertEqual(t.load_rollout(p), [{"a": 1}, {"b": 2}])

    def test_live_stage_gates_on_version_and_auth_before_any_inference(self):
        import inspect

        src = inspect.getsource(t.stage_b)
        call = src.index("runner.run_turn(")
        self.assertLess(src.index("codexcli.check_compat("), call)
        self.assertLess(src.index("codexcli.check_auth("), call)
        self.assertLess(src.index("codexcli.sandbox_preflight("), call)
        self.assertIn("PRODUCTION ARGV + RUNNER", src)
        self.assertIn("not the MCP/service path", src)


if __name__ == "__main__":
    unittest.main()
