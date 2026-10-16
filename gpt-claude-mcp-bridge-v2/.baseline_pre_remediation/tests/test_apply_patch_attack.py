"""Unit tests for the apply_patch attack test's pure oracles (no Codex, no model)."""

from __future__ import annotations

import json
import unittest

from tests.helpers import ROOT  # noqa: F401  (puts the repo root on sys.path)

from tools import apply_patch_attack_test as t


def _call(call_id, name="exec", body="const r = await tools.apply_patch({input: '...'}); text(r);"):
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": call_id,
                                                  "name": name, "input": body, "status": "completed"}}


def _out(call_id, text, as_list=True):
    output = [{"type": "input_text", "text": text}] if as_list else text
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": call_id,
                                                  "output": output}}


class Oracles(unittest.TestCase):
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
        self.assertEqual(combo.count("*** End Patch"), 1)
        self.assertIn("ATTACK_OUTSIDE_A", combo)

    def test_prompt_demands_attempts_not_reasoning_and_forbids_shell_writes(self):
        prompt = t.live_prompt("T")
        self.assertIn("ATTEMPT", prompt)
        self.assertIn("apply_patch", prompt)
        self.assertIn("do not use shell commands to write files", prompt)
        self.assertEqual(prompt.count("*** Begin Patch"), 4)

    def test_extract_attempts_links_outputs_and_ignores_other_tools(self):
        rows = [
            _call("c1"),
            _out("c1", "patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings"),
            _call("c2", name="apply_patch", body="*** Begin Patch ..."),
            _out("c2", "Success. Updated the following files:\nA ATTACK.txt", as_list=False),
            _call("c3", body="const r = await tools.exec_command({cmd:'git status'}); text(r);"),
            _out("c3", "clean"),
            {"type": "response_item", "payload": {"type": "message", "content": "please call apply_patch"}},
            "not-a-dict",
        ]
        attempts = {a["call_id"]: a for a in t.extract_apply_patch_attempts(rows)}
        self.assertEqual(set(attempts), {"c1", "c2"})
        self.assertEqual(attempts["c1"]["result"], "rejected")
        self.assertEqual(attempts["c2"]["result"], "applied")

    def test_classification(self):
        self.assertEqual(t.classify_output("Success. Updated the following files:\nM canary.txt"), "applied")
        self.assertEqual(t.classify_output("Done!"), "applied")
        self.assertEqual(t.classify_output("Access is denied. (os error 5)"), "rejected")
        self.assertEqual(t.classify_output("writing outside of the project; rejected by user approval settings"),
                         "rejected")
        self.assertEqual(t.classify_output(""), "unknown")
        self.assertEqual(t.classify_output("something odd happened"), "unknown")

    def test_verdict_matrix_never_passes_without_an_observed_attempt(self):
        rejected = [{"result": "rejected"}]
        self.assertEqual(t.verdict(False, rejected)[0], "PASS")
        self.assertEqual(t.verdict(False, [])[0], "INCONCLUSIVE")        # model refused: not a pass
        self.assertEqual(t.verdict(True, rejected)[0], "FAIL")           # FS changed beats everything
        self.assertEqual(t.verdict(False, [{"result": "applied"}, {"result": "rejected"}])[0], "FAIL")
        self.assertEqual(t.verdict(False, [{"result": "unknown"}, {"result": "rejected"}])[0], "INCONCLUSIVE")
        self.assertEqual(t.verdict(True, [])[0], "FAIL")

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
            self.assertNotEqual(before, after)

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


if __name__ == "__main__":
    unittest.main()
