"""Which of a round's NEW tests actually discriminate the fix?  Run them against the pre-fix source.

    python -B tools/prefix_check.py --snapshot .baseline_post_remediation4 --module tests.test_review5

The overlay is exactly: the snapshot's ``bridge/`` and ``tools/`` (the code the previous review
verified) + the CURRENT test harness (``tests/__init__.py``, ``helpers.py``, ``fake_codex.py``,
``hard_kill_helper.py``, ``fixtures/``) + the CURRENT test module.  The module must import
new-API modules defensively (``try: from bridge import execpath / except ImportError: execpath =
None``), so that an unfixed tree produces test FAILURES, not an import error that hides which tests
would have caught what.

The record is made checkable end to end (S10):

* every file copied from the snapshot is verified against the snapshot's MANIFEST.json hashes, and
  every harness/module file is recorded with the hash of the CURRENT bytes (``overlay``); the
  prompt builder re-verifies the COMPLETE overlay (T8) from the bytes on disk;
* the test identities the module defines are reconstructed from its SYNTAX TREE
  (``tools/prefix_logic.py``, non-executing) and recorded (``expected_test_ids``); they must equal the
  tests that ran; a loader failure (the module could not even be imported) is an error of the CHECK,
  never a "discriminating" test;
* each failing outcome is classified: ``assertion`` (behaviour differed), ``api_missing`` (the new
  API does not exist before the fix: weaker evidence, kept distinct) or ``behavioural_error``;
* the raw log's hash and its machine-readable result line are retained.

Writes ``docs/evidence/prefix_check.json`` (bound to the current source hash) and
``docs/evidence/prefix_check.log``.  A test that PASSES on the pre-fix source does not discriminate:
it is listed explicitly (regression guards are expected; anything else is a weak test).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUNNER = r'''
import json, sys, unittest

class Result(unittest.TextTestResult):
    outcomes = {}
    def addSuccess(self, test):
        super().addSuccess(test); Result.outcomes[test.id()] = "pass"
    def addFailure(self, test, err):
        super().addFailure(test, err); Result.outcomes[test.id()] = "fail"
    def addError(self, test, err):
        super().addError(test, err); Result.outcomes[test.id()] = "error"
    def addSkip(self, test, reason):
        super().addSkip(test, reason); Result.outcomes[test.id()] = "skip"

suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
unittest.TextTestRunner(resultclass=Result, verbosity=2).run(suite)
print("RESULT_JSON:" + json.dumps(Result.outcomes))
'''

from tools.prefix_logic import classify_errors, expected_test_ids  # noqa: E402  (pure, non-executing)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", required=True, help="snapshot directory under the v2 root")
    parser.add_argument("--module", required=True, help="dotted test module, e.g. tests.test_review5")
    parser.add_argument("--guards", nargs="*", default=[],
                        help="test ids (suffix match) that are DECLARED regression guards")
    ns = parser.parse_args()

    from tools.record_evidence import fingerprint

    snap = ROOT / ns.snapshot
    manifest = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    by_path = {f["path"]: f["sha256"] for f in manifest.get("files", [])}
    module_file = ROOT / (ns.module.replace(".", "/") + ".py")

    import unittest
    expected = unittest.defaultTestLoader.loadTestsFromName(ns.module).countTestCases()
    expected_ids = sorted(expected_test_ids(module_file.read_text(encoding="utf-8"), ns.module))

    overlay: list[dict] = []
    problems: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="prefix_check_"))
    try:
        shutil.copytree(snap / "bridge", tmp / "bridge")
        shutil.copytree(snap / "tools", tmp / "tools")
        (tmp / "tests").mkdir()
        for name in ("__init__.py", "helpers.py", "fake_codex.py", "hard_kill_helper.py"):
            shutil.copy2(ROOT / "tests" / name, tmp / "tests" / name)
        shutil.copytree(ROOT / "tests" / "fixtures", tmp / "tests" / "fixtures")
        for name in ("architect_bridge.py", "bridge_admin.py"):
            if (snap / name).is_file():
                shutil.copy2(snap / name, tmp / name)
        shutil.copy2(module_file, tmp / "tests" / module_file.name)

        # verify what was ACTUALLY copied: snapshot files against the manifest, harness files
        # against the live tree's current bytes
        for path in sorted(tmp.rglob("*.py")) + sorted((tmp / "tests" / "fixtures").glob("*.json")):
            rel = path.relative_to(tmp).as_posix()
            digest = _sha(path)
            if rel.startswith(("bridge/", "tools/")) or rel in ("architect_bridge.py", "bridge_admin.py"):
                origin = "snapshot"
                if by_path.get(rel) != digest:
                    problems.append(f"overlay file {rel} does not match the snapshot manifest")
            else:
                origin = "current_tree"
                live = ROOT / rel
                if not live.is_file() or _sha(live) != digest:
                    problems.append(f"overlay file {rel} does not match the current tree")
            overlay.append({"path": rel, "sha256": digest, "origin": origin})
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-B", "-c", RUNNER, ns.module], cwd=str(tmp),
                              capture_output=True, text=True, timeout=3000, stdin=subprocess.DEVNULL)
        seconds = round(time.monotonic() - started, 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    text = proc.stdout + proc.stderr
    outcomes: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("RESULT_JSON:"):
            outcomes = json.loads(line[len("RESULT_JSON:"):])
    loader_errors = sorted(t for t in outcomes if t.startswith("unittest.loader."))
    if loader_errors:
        problems.append(f"{len(loader_errors)} loader failure(s): the module did not import on the pre-fix source")
    if len(outcomes) != expected:
        problems.append(f"{len(outcomes)} tests ran but the module defines {expected}")
    if set(outcomes) != set(expected_ids):
        problems.append("the tests that ran are not the tests the module's syntax tree defines")
    guards = [t for t in outcomes if any(t.endswith(g) for g in ns.guards)]
    passing = sorted(t for t, o in outcomes.items() if o == "pass")
    undeclared = [t for t in passing if t not in guards]
    skipped = sorted(t for t, o in outcomes.items() if o == "skip")
    kinds = classify_errors(text, outcomes)
    ev = ROOT / "docs" / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    log_bytes = text.encode("utf-8", errors="replace")
    (ev / "prefix_check.log").write_bytes(log_bytes)
    counts = {k: sum(1 for v in kinds.values() if v == k)
              for k in ("assertion", "api_missing", "behavioural_error")}
    record = {
        "evidence_class": "implementer_executed",
        "what": "the round's new tests run against the PRE-FIX source (overlay described in "
                "tools/prefix_check.py)",
        "source_combined_sha256": fingerprint()["combined_sha256"],
        "snapshot": ns.snapshot,
        "snapshot_manifest_combined_sha256_all_files": manifest.get("combined_sha256_all_files"),
        "module": ns.module,
        "tests": len(outcomes),
        "expected_tests": expected,
        "expected_test_ids": expected_ids,
        "module_file": module_file.relative_to(ROOT).as_posix(),
        "fail_or_error_on_prefix": sum(1 for o in outcomes.values() if o in ("fail", "error")),
        "failure_kinds": counts,
        "failure_kind_by_test": kinds,
        "loader_errors": loader_errors,
        "skipped": skipped,
        "overlay_problems": problems,
        "overlay": overlay,
        "pass_on_prefix": passing,
        "declared_guards": guards,
        "undeclared_passes": undeclared,
        "seconds": seconds,
        "outcomes": outcomes,
        "raw_log": "prefix_check.log",
        "raw_log_sha256": hashlib.sha256(log_bytes).hexdigest(),
    }
    (ev / "prefix_check.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{record['tests']}/{expected} tests run against {ns.snapshot}: {record['fail_or_error_on_prefix']} "
          f"fail/error ({counts['assertion']} assertion, {counts['api_missing']} api_missing, "
          f"{counts['behavioural_error']} behavioural error), {len(passing)} pass "
          f"({len(guards)} declared guards, {len(undeclared)} undeclared)")
    for t in undeclared:
        print(f"  UNDECLARED PASS (weak test or guard to declare): {t}")
    for pr in problems:
        print(f"  PROBLEM: {pr}")
    return 0 if outcomes and not undeclared and not problems and not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
