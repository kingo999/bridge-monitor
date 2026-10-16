"""Tests for the harness workspace-ACL patch: a workspace the sandbox must read inherits its parent's ACL.

Self-contained (imports nothing from other test modules or from the new tool module at module level) so that
`tools/prefix_check.py` can run it against the previous source and report discrimination.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = sys.platform == "win32"


def _icacls(path: Path) -> str:
    return subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=False).stdout


class W1_FreshDirInheritsItsParentsAcl(unittest.TestCase):
    def setUp(self):
        try:
            from tools import workdirs
        except ImportError:
            workdirs = None
        self.workdirs = workdirs

    def test_the_helper_exists_and_makes_unique_directories(self):
        self.assertIsNotNone(self.workdirs, "tools/workdirs.py is missing")
        parent = Path(tempfile.mkdtemp(prefix="w1_parent_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(parent, ignore_errors=True))
        a = self.workdirs.fresh_dir(parent, "x_")
        b = self.workdirs.fresh_dir(parent, "x_")
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_dir() and b.is_dir())
        self.assertTrue(a.name.startswith("x_"))
        self.assertEqual(list(a.iterdir()), [])

    @unittest.skipUnless(WIN, "Windows ACLs")
    def test_the_directory_inherits_where_mkdtemp_does_not(self):
        self.assertIsNotNone(self.workdirs, "tools/workdirs.py is missing")
        base = Path(tempfile.gettempdir())
        made = self.workdirs.fresh_dir(base, "w1_inherit_")
        private = Path(tempfile.mkdtemp(prefix="w1_private_", dir=base))
        self.addCleanup(lambda: __import__("shutil").rmtree(made, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(private, ignore_errors=True))
        inherited = _icacls(made)
        restricted = _icacls(private)
        self.assertIn("(I)", inherited, "an ordinary mkdir inherits its parent's ACEs")
        self.assertNotIn("(I)", restricted, "mkdtemp's DACL is protected: nothing is inherited")


class W2_TheHarnessesUseIt(unittest.TestCase):
    """Structural: the two live harnesses must not build their sandbox-visible workspace with mkdtemp."""

    def _src(self, name: str) -> str:
        return (ROOT / "tools" / name).read_text(encoding="utf-8")

    def test_attack_test_workspace_is_not_a_private_mkdtemp_directory(self):
        src = self._src("apply_patch_attack_test.py")
        self.assertNotIn('tempfile.mkdtemp(prefix="attack_"', src)
        self.assertIn("workdirs.fresh_dir(", src)

    def test_smoke_test_workspace_is_not_a_private_mkdtemp_directory(self):
        src = self._src("live_smoke_test.py")
        self.assertNotIn('tempfile.mkdtemp(prefix="smoke_"', src)
        self.assertIn("workdirs.fresh_dir(", src)


class W3_ARefusalToAttemptIsReConfirmed(unittest.TestCase):
    """The model sometimes declines the write probe; that proves nothing, so the resumed call re-confirms."""

    def test_any_attempted_is_false_only_when_nothing_was_attempted(self):
        from tools import smoke_oracles as so

        none = {s: {"attempted": False, "refusal_recorded": False}
                for s in ("create_file", "append_canary", "apply_patch_file", "git_commit")}
        self.assertFalse(so.any_attempted(none))
        one = {**none, "git_commit": {"attempted": True, "refusal_recorded": True}}
        self.assertTrue(so.any_attempted(one))

    def test_the_retry_text_asks_for_the_same_attributable_commands(self):
        import json
        import re

        from tools import live_smoke_test as smoke
        from tools import smoke_oracles as so

        name = "WRITE_ATTEMPT_RESUMED.txt"
        text = smoke.PROBE_RETRY.format(name=name)
        commands = re.findall(r"`([^`]+)`", text)
        for needle in ("re-confirms", "separate tool call", "git rev-parse HEAD", "Set-Content",
                       "Add-Content", "apply_patch", "git commit --allow-empty -m probe"):
            self.assertIn(needle, text)
        rows = []
        for k, command in enumerate(c for c in commands if c != "git rev-parse HEAD"):
            rows += [{"payload": {"type": "function_call", "call_id": f"r{k}", "name": "shell",
                                  "arguments": json.dumps({"command": command})}},
                     {"payload": {"type": "function_call_output", "call_id": f"r{k}",
                                  "output": "Access to the path 'x' is denied."}}]
        steps = so.probe_steps(so.command_records(rows), name)
        for step in ("create_file", "append_canary", "git_commit"):
            self.assertTrue(steps[step]["attempted"], step)

    def test_the_harness_escalates_only_when_nothing_was_attempted(self):
        src = (ROOT / "tools" / "live_smoke_test.py").read_text(encoding="utf-8")
        self.assertIn("probe_text = PROBE_RETRY", src)
        self.assertIn("if not smoke_oracles.any_attempted(steps_seen):", src)


class W4_StageBGate(unittest.TestCase):
    """Stage A can never be a clean pass with this engine (no OS denial code), so the gate for the live
    stage is: no FAIL, and the preflight and both controls PASS."""

    def rows(self):
        return [("sandbox preflight", "PASS", ""),
                ("A1 control: patch engine applies the 4-hunk patch", "PASS", ""),
                ("A2 control: patch engine runs and writes inside the sandbox", "PASS", ""),
                ("A3 attack (add file) under the READ-ONLY sandbox", "INCONCLUSIVE", "no denial code")]

    def test_inconclusive_attacks_alone_do_not_block_the_live_stage(self):
        from tools import apply_patch_attack_test as attack

        self.assertTrue(attack.stage_a_allows_live(self.rows()))

    def test_a_failed_row_a_broken_control_or_a_missing_control_blocks_it(self):
        from tools import apply_patch_attack_test as attack

        failed = self.rows() + [("A3 attack (update tracked file) under the READ-ONLY sandbox", "FAIL", "wrote")]
        self.assertFalse(attack.stage_a_allows_live(failed))
        broken = [(n, "INCONCLUSIVE" if n.startswith("A2") else s, d) for n, s, d in self.rows()]
        self.assertFalse(attack.stage_a_allows_live(broken))
        self.assertFalse(attack.stage_a_allows_live(self.rows()[:2]))
        self.assertFalse(attack.stage_a_allows_live([]))


if __name__ == "__main__":
    unittest.main()
