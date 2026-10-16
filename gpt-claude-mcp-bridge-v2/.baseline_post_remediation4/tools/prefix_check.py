"""Which of a round's NEW tests actually discriminate the fix?  Run them against the pre-fix source.

    python -B tools/prefix_check.py --snapshot .baseline_post_remediation3 --module tests.test_review4

The overlay is exactly: the snapshot's ``bridge/`` and ``tools/`` (the code the previous review
verified) + the CURRENT test harness (``tests/__init__.py``, ``helpers.py``, ``fake_codex.py``,
``hard_kill_helper.py``, ``fixtures/``) + the CURRENT test module.  The module must import
new-API modules defensively (``try: from bridge import execpath / except ImportError: execpath =
None``), so that an unfixed tree produces test FAILURES, not an import error that hides which tests
would have caught what.

Writes ``docs/evidence/prefix_check.json`` (per-test outcome, bound to the current source hash) and
the raw log ``docs/evidence/prefix_check.log``.  A test that PASSES on the pre-fix source does not
discriminate: it is listed explicitly (regression guards are expected; anything else is a weak test).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", required=True, help="snapshot directory under the v2 root")
    parser.add_argument("--module", required=True, help="dotted test module, e.g. tests.test_review4")
    parser.add_argument("--guards", nargs="*", default=[],
                        help="test ids (suffix match) that are DECLARED regression guards")
    ns = parser.parse_args()

    from tools.record_evidence import fingerprint

    snap = ROOT / ns.snapshot
    manifest = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    module_file = ROOT / (ns.module.replace(".", "/") + ".py")
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
    guards = [t for t in outcomes if any(t.endswith(g) for g in ns.guards)]
    passing = sorted(t for t, o in outcomes.items() if o == "pass")
    undeclared = [t for t in passing if t not in guards]
    ev = ROOT / "docs" / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    log_bytes = text.encode("utf-8", errors="replace")
    (ev / "prefix_check.log").write_bytes(log_bytes)
    record = {
        "evidence_class": "implementer_executed",
        "what": "the round's new tests run against the PRE-FIX source (overlay described in "
                "tools/prefix_check.py)",
        "source_combined_sha256": fingerprint()["combined_sha256"],
        "snapshot": ns.snapshot,
        "snapshot_manifest_combined_sha256_all_files": manifest.get("combined_sha256_all_files"),
        "module": ns.module,
        "tests": len(outcomes),
        "fail_or_error_on_prefix": sum(1 for o in outcomes.values() if o in ("fail", "error")),
        "pass_on_prefix": passing,
        "declared_guards": guards,
        "undeclared_passes": undeclared,
        "seconds": seconds,
        "outcomes": outcomes,
        "raw_log": "prefix_check.log",
        "raw_log_sha256": hashlib.sha256(log_bytes).hexdigest(),
    }
    (ev / "prefix_check.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{record['tests']} tests run against {ns.snapshot}: {record['fail_or_error_on_prefix']} fail/error "
          f"(discriminate), {len(passing)} pass ({len(guards)} declared guards, {len(undeclared)} undeclared)")
    for t in undeclared:
        print(f"  UNDECLARED PASS (weak test or guard to declare): {t}")
    return 0 if outcomes and not undeclared else 1


if __name__ == "__main__":
    raise SystemExit(main())
