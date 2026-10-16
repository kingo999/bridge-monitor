"""Prepare (NOT send) the verification prompt for GPT's sixth-round review.

    python -B tools/make_verification_prompt.py

Builds a self-contained prompt from the current tree and writes
``docs/gpt_verification/verification_prompt.md`` and ``verification_manifest.json``.  This tool never
runs Codex, never uses the subscription allowance, and never sends anything: sending is a separate,
human-authorised step.

It refuses to build when the recorded evidence is not bound to the *current* source hash, when the
tree changed while the tests ran, when the recorded test run did not pass cleanly (exit code, skips,
retained raw log), or when a required file is missing, so a prompt can never be prepared against
stale or unattributable evidence.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT = ".baseline_post_remediation5"      # exactly the state GPT verified in the previous round
OUT_DIR = Path("docs") / "gpt_verification"
SIZE_WARN_BYTES = 880_000

# Embedded in full: everything GPT needs to verify the claims without being told to trust them.
# A file listed here that is RAW-BYTE identical to the snapshot GPT verified last round is not embedded
# again (it is hash-listed in the manifest, mode `unchanged_since_verified_snapshot`) unless it is in
# ALWAYS_FULL.  Round 5 omitted too much (R11, V12, V15, N11, N14 became CANNOT VERIFY): every file the
# round-5 review named as missing is therefore ALWAYS_FULL now.
FULL = [
    "README.md",
    "REVIEW_PACKAGE.md",
    "docs/REMEDIATION_CHECKLIST_6.md",
    "docs/remediation6_stat.txt",
    "architect_bridge.py",
    "bridge_admin.py",
    "bridge/__init__.py",
    "bridge/admin.py",
    "bridge/codexcli.py",
    "bridge/config.py",
    "bridge/errors.py",
    "bridge/execpath.py",
    "bridge/fingerprint.py",
    "bridge/gitsafe.py",
    "bridge/ids.py",
    "bridge/jobobject.py",
    "bridge/redact.py",
    "bridge/runner.py",
    "bridge/server.py",
    "bridge/service.py",
    "bridge/state.py",
    "bridge/telemetry.py",
    "tools/apply_patch_attack_test.py",
    "tools/record_evidence.py",
    "tools/make_verification_prompt.py",
    "tools/send_verification.py",
    "tools/prefix_check.py",
    "tools/prefix_logic.py",
    "tools/privacy.py",
    "tools/smoke_oracles.py",
    "tools/live_smoke_test.py",
    "tools/make_telemetry_examples.py",
    "tools/make_remediation_diff.py",
    "tools/make_v1_diff.py",
    "tools/v1_manifest.py",
    "tools/verify_lock.py",
    "tests/fake_codex.py",
    "tests/helpers.py",
    "tests/hard_kill_helper.py",
    "tests/test_review6.py",
    "tests/test_review5.py",
    "tests/test_review4.py",
    "tests/test_review2.py",
    "tests/test_apply_patch_attack.py",
    "tests/test_review3.py",
    "tests/test_remediation.py",
    "tests/test_service.py",
    "requirements.lock.txt",
]
ALWAYS_FULL = frozenset({
    "README.md", "REVIEW_PACKAGE.md", "docs/REMEDIATION_CHECKLIST_6.md", "docs/remediation6_stat.txt",
    "tests/test_review6.py",
    # named as missing material by the round-5 review
    "bridge/config.py", "bridge/errors.py", "bridge/fingerprint.py", "bridge/gitsafe.py", "bridge/ids.py",
    "bridge/jobobject.py", "bridge/telemetry.py", "bridge/server.py", "architect_bridge.py", "bridge_admin.py",
    "tests/fake_codex.py", "tests/hard_kill_helper.py", "tools/live_smoke_test.py", "tools/privacy.py",
    "tests/test_review2.py", "tests/test_review4.py", "tests/test_apply_patch_attack.py",
})
#: bridge/ files changed since the verified snapshot are also shown as ONE computed diff
#: (a few lines of context), so newly introduced behaviour is separable from retained code.
SRC_DIFF_PREFIXES = ("bridge/",)
DIFF_CONTEXT = 1
# Files GPT already saw in full and did not ask for again: only what changed since (against SNAPSHOT).
DIFF_ONLY = [
    "tests/test_state.py",
    "tests/test_server_admin.py",
    "tests/test_config_argv_auth.py",
]
# Embedded in full, matched by pattern (the set varies by run).  Review / request files: newest only.
GLOB_FULL = (
    "tests/fixtures/*.json",
    "tests/fixtures/**/*.json",
    "bridge/local_worker/**/*.py",
    "docs/evidence/source_fingerprint.json",
    "docs/evidence/capability_policy_real_cli.json",
    "docs/evidence/tests_*.json",
    "docs/evidence/integrity.json",
    "docs/evidence/cli_observations.json",
    "docs/evidence/prefix_check.json",
    "docs/evidence/prefix_check.log",
    "docs/gpt_verification/gpt_verification_*.md",
    "docs/gpt_verification/request_*.json",
    "docs/gpt_verification/verification_manifest_round*.json",
    ".baseline_post_remediation5/MANIFEST.json",
)
#: Retained raw logs that are hash-listed only (size); the gate accounts for every per-test line.
GLOB_LISTED = ("docs/evidence/tests_*.log",)
NEWEST_ONLY = ("docs/gpt_verification/gpt_verification_*.md", "docs/gpt_verification/request_*.json",
               "docs/gpt_verification/verification_manifest_round*.json")
# Hash-listed only (no embedded text); available on request.
LISTED = [
    "tools/make_lock.py", "tools/capture_v1_fixtures.py", "requirements.in", "docs/remediation_stat.txt",
    "docs/remediation2_stat.txt", "docs/remediation3_stat.txt", "docs/remediation4_stat.txt",
    "docs/remediation5_stat.txt", "docs/remediation5.diff", "docs/telemetry_examples_remediation.jsonl",
    "docs/v1_baseline_before.json",
]
EVIDENCE_TESTS_GLOB = "docs/evidence/tests_*.json"
REQUIRED_NONEMPTY_GLOBS = ("docs/evidence/tests_*.log", "docs/evidence/prefix_check.log",
                           "docs/gpt_verification/gpt_verification_*.md")


# --------------------------------------------------------------------------- evidence checks
def current_source_hash() -> str:
    from tools import record_evidence

    return record_evidence.fingerprint()["combined_sha256"]


def evidence_problems(root: Path, source_sha: str) -> list[str]:
    """Why the recorded evidence cannot be attached to the current source ([] = fine)."""
    problems: list[str] = []
    ev = root / "docs" / "evidence"

    def load(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    fp = load(ev / "source_fingerprint.json")
    if fp is None:
        problems.append("docs/evidence/source_fingerprint.json is missing or unreadable")
    elif fp.get("combined_sha256") != source_sha:
        problems.append("source_fingerprint.json is bound to a different source hash (stale)")

    pol = load(ev / "capability_policy_real_cli.json")
    if pol is None:
        problems.append("docs/evidence/capability_policy_real_cli.json is missing or unreadable")
    elif pol.get("source_combined_sha256") != source_sha:
        problems.append("capability_policy_real_cli.json is bound to a different source hash (stale)")

    # R9: every other embedded evidence record is bound to the same hash, and the summary of a test
    # record is reconciled with its retained log (not just the log's hash)
    for name in ("integrity.json", "cli_observations.json"):
        rec = load(ev / name)
        if rec is None:
            problems.append(f"docs/evidence/{name} is missing or unreadable")
        elif rec.get("source_combined_sha256") != source_sha:
            problems.append(f"{name} is bound to a different source hash (stale)")
    problems.extend(prefix_problems(root, source_sha))
    runs = sorted(root.glob(EVIDENCE_TESTS_GLOB))
    if not runs:
        problems.append("no docs/evidence/tests_*.json test record exists")
    for path in runs:
        rec = load(path)
        if rec is None:
            problems.append(f"{path.name} is unreadable")
            continue
        if rec.get("source_combined_sha256") != source_sha:
            problems.append(f"{path.name} is bound to a different source hash (stale); delete or re-record it")
        elif rec.get("source_sha256_after_run") != source_sha:
            problems.append(f"{path.name}: the tree changed while the tests ran, or the record has no "
                            "after-run fingerprint (V13); re-record it")
        elif rec.get("outcome") != "OK" or rec.get("failing") or not rec.get("tests_run"):
            problems.append(f"{path.name} does not record a passing run")
        elif rec.get("exit_code") != 0:
            problems.append(f"{path.name} does not record a passing run (test process exit "
                            f"{rec.get('exit_code')!r})")
        elif rec.get("skipped"):
            problems.append(f"{path.name} does not record a passing run ({rec.get('skipped')} skipped; the "
                            "declared policy is zero skips)")
        else:
            log = rec.get("raw_log")
            log_path = ev / str(log) if log else None
            if not log_path or not log_path.is_file():
                problems.append(f"{path.name}: the retained raw test output is missing")
            elif hashlib.sha256(log_path.read_bytes()).hexdigest() != rec.get("raw_log_sha256"):
                problems.append(f"{path.name}: the retained raw test output does not match its recorded hash")
            else:
                problems.extend(f"{path.name}: {p}" for p in log_problems(
                    log_path.read_text(encoding="utf-8", errors="replace"), rec.get("tests_run")))
            if rec.get("executed_from") != "staged_copy" or rec.get("source_sha256_staged_copy") != source_sha:
                problems.append(f"{path.name}: the tests were not executed from a staged copy bound to this hash")
            elif rec.get("source_sha256_staged_copy_after_run") != source_sha:
                problems.append(f"{path.name}: the staged copy was not re-fingerprinted after the run, or it "
                                "changed during the run (S9)")
    return problems


def prefix_problems(root: Path, source_sha: str) -> list[str]:
    """The pre-fix discrimination record must be checkable end to end (S10): bound to this hash, its raw
    log intact and consistent with the recorded outcomes, the snapshot overlay verified, the number of
    tests equal to what the module defines, no loader failures, no undeclared passes."""
    ev = root / "docs" / "evidence"
    try:
        rec = json.loads((ev / "prefix_check.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["docs/evidence/prefix_check.json is missing or unreadable"]
    out: list[str] = []
    if rec.get("source_combined_sha256") != source_sha:
        out.append("prefix_check.json is bound to a different source hash (stale)")
    log_path = ev / str(rec.get("raw_log") or "prefix_check.log")
    outcomes = rec.get("outcomes")
    if not isinstance(outcomes, dict) or not outcomes:
        out.append("prefix_check.json records no outcomes")
        outcomes = {}
    if not log_path.is_file():
        out.append("prefix_check: the retained raw log is missing")
    elif hashlib.sha256(log_path.read_bytes()).hexdigest() != rec.get("raw_log_sha256"):
        out.append("prefix_check: the retained raw log does not match its recorded hash")
    else:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        result_line = next((ln for ln in text.splitlines() if ln.startswith("RESULT_JSON:")), None)
        try:
            logged = json.loads(result_line[len("RESULT_JSON:"):]) if result_line else None
        except ValueError:
            logged = None
        if logged != outcomes:
            out.append("prefix_check: the per-test outcomes in the record do not match the retained log")
    if rec.get("tests") != len(outcomes) or rec.get("expected_tests") != rec.get("tests"):
        out.append(f"prefix_check: {len(outcomes)} outcomes, record says {rec.get('tests')!r} ran, module defines "
                   f"{rec.get('expected_tests')!r}")
    if rec.get("loader_errors"):
        out.append("prefix_check: the module failed to load on the pre-fix source (loader errors)")
    if rec.get("undeclared_passes"):
        out.append("prefix_check: tests pass on the pre-fix source without being declared guards")
    if rec.get("skipped"):
        out.append("prefix_check: tests were skipped")
    if rec.get("overlay_problems"):
        out.append("prefix_check: the overlay did not match the snapshot manifest / current harness")
    # ---- everything below is RECONSTRUCTED from bytes on disk, not read back from the record (T8)
    from tools import prefix_logic

    module = str(rec.get("module") or "")
    mod_path = prefix_logic.module_file(root, module) if module else None
    if mod_path is None or not mod_path.is_file():
        out.append("prefix_check: the module under test is not in the tree")
    else:
        try:
            expected = prefix_logic.expected_test_ids(mod_path.read_text(encoding="utf-8"), module)
        except (SyntaxError, ValueError, OSError):
            expected = set()
            out.append("prefix_check: the module under test cannot be parsed")
        if expected and set(outcomes) != expected:
            out.append("prefix_check: the recorded tests are not the tests the module's syntax tree defines "
                       f"({len(outcomes)} recorded, {len(expected)} defined)")
        if expected and set(rec.get("expected_test_ids") or []) != expected:
            out.append("prefix_check: the record's expected test identities are not the module's")
    if outcomes and isinstance(rec.get("failure_kind_by_test"), dict):
        text_for_kinds = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        if prefix_logic.classify_errors(text_for_kinds, outcomes) != rec.get("failure_kind_by_test"):
            out.append("prefix_check: the recorded failure kinds do not match the retained log")
    elif outcomes:
        out.append("prefix_check: the record has no failure classification")

    snap = root / str(rec.get("snapshot") or "")
    try:
        manifest = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if manifest is None:
        out.append("prefix_check: the snapshot named by the record has no readable MANIFEST.json")
        return out
    if manifest.get("combined_sha256_all_files") != rec.get("snapshot_manifest_combined_sha256_all_files"):
        out.append("prefix_check: the snapshot manifest is not the one the check ran against")
    known = {f["path"]: f["sha256"] for f in manifest.get("files", []) if isinstance(f, dict)}
    overlay = {e.get("path"): e for e in (rec.get("overlay") or []) if isinstance(e, dict)}
    if not overlay:
        out.append("prefix_check: the record carries no overlay file list")
    # the overlay must be COMPLETE: every snapshot source file, every harness file, the module, fixtures
    required_snapshot = sorted(p for p in known if p.endswith(".py") and
                               (p.startswith(("bridge/", "tools/")) or "/" not in p))
    required_current = [p for p in prefix_logic.HARNESS_FILES if (root / p).is_file()]
    required_current += sorted(p.relative_to(root).as_posix() for p in (root / "tests" / "fixtures").glob("*.json")
                               ) if (root / "tests" / "fixtures").is_dir() else []
    if mod_path is not None and mod_path.is_file():
        required_current.append(mod_path.relative_to(root).as_posix())
    missing = [p for p in required_snapshot + required_current if p not in overlay]
    if missing:
        out.append(f"prefix_check: the overlay omits {len(missing)} file(s) that executed, e.g. {missing[0]}")
    for path in required_snapshot:
        entry = overlay.get(path)
        if entry is None:
            continue
        actual = snap / path
        if (entry.get("origin") != "snapshot" or known.get(path) != entry.get("sha256") or not actual.is_file()
                or hashlib.sha256(actual.read_bytes()).hexdigest() != entry.get("sha256")):
            out.append(f"prefix_check: overlay file {path} is not the snapshot's bytes")
            break
    for path in required_current:
        entry = overlay.get(path)
        if entry is None:
            continue
        live = root / path
        if (entry.get("origin") != "current_tree" or not live.is_file()
                or hashlib.sha256(live.read_bytes()).hexdigest() != entry.get("sha256")):
            out.append(f"prefix_check: overlay harness/module file {path} differs from the current tree")
            break
    return out


def log_problems(text: str, tests_run: object) -> list[str]:
    """The retained log must itself report the passing, skip-free run the summary claims (R9)."""
    import re

    out: list[str] = []
    ran = re.search(r"^Ran (\d+) tests? in", text, re.MULTILINE)
    if not ran:
        out.append("the retained log has no `Ran N tests` line")
    elif tests_run != int(ran.group(1)):
        out.append(f"the retained log reports {ran.group(1)} tests but the record says {tests_run!r}")
    if not re.search(r"^OK\s*$", text, re.MULTILINE):
        out.append("the retained log has no `OK` line")
    # per-test accounting (S-review: "summary footer" is not accounting): one header and one `ok`
    # result per test the record claims
    if isinstance(tests_run, int):
        # one header and one `ok` result per test, in order, each identity exactly once (T8)
        seen: list[str] = []
        pending: str | None = None
        paired = 0
        header = re.compile(r"^\S+ \(([\w.]+)\)(?: \.\.\. (.*))?$")
        for line in text.splitlines():
            m = header.match(line)
            if m:
                if pending is not None:
                    out.append(f"the retained log has no result for {pending}")
                    pending = None
                pending = m.group(1)
                seen.append(pending)
                rest = m.group(2)
                if rest is not None and rest.strip() == "ok":
                    paired += 1
                    pending = None
                continue
            if pending is not None and (line.strip() == "ok" or line.rstrip().endswith(" ... ok")):
                paired += 1
                pending = None
        if pending is not None:
            out.append(f"the retained log has no result for {pending}")
        if len(seen) != tests_run:
            out.append(f"the retained log lists {len(seen)} per-test headers but the record says {tests_run}")
        if len(set(seen)) != len(seen):
            out.append("the retained log repeats a test identity")
        if paired != tests_run:
            out.append(f"the retained log has {paired} `ok` test results but the record says {tests_run}")
    if re.search(r"^(FAIL|ERROR):", text, re.MULTILINE):
        out.append("the retained log contains FAIL/ERROR entries")
    if re.search(r"skipped=\d+", text):
        out.append("the retained log reports skipped tests")
    return out


# --------------------------------------------------------------------------- prompt text
INSTRUCTIONS = """You are the GPT ARCHITECT performing a VERIFICATION review of a sixth remediation of the
"GPT Architect <-> Claude Code MCP Bridge v2". This is an architecture and contract review. The human decides
activation. RULES FOR THIS CALL:

- Everything you need is embedded below. Do NOT run commands, read other files, use the web, spawn agents or
  generate images. You are read-only; do not attempt any write or tool call.
- Your output is ADVISORY. It is not approval. Do not approve activation, registration or any live run.
- The implementer (Claude Code) wrote both the code and this prompt. Treat every statement of the form
  "implementer claim" as a hypothesis to FALSIFY, not as a fact. For each claim say one of:
  CONFIRMED FROM SOURCE / CONTRADICTED / CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL, and cite the file and
  function (or test) that you relied on.
- All test and evidence results below were produced by the implementer on the human's machine and are
  recorded, not independently verified; you cannot re-run them. The test records, the pre-fix raw output and
  the before/after/staged source fingerprints are embedded so you can judge provenance (the passing run's
  raw log is hash-listed only; the gate in tools/make_verification_prompt.py::log_problems accounts for every
  per-test line, pairing and uniqueness), but judge whether the TESTS AS WRITTEN would detect the defects,
  not whether the recorded totals are green.
- No live model call, registration or activation has been made for THIS source. The only live results that
  exist (smoke test, attack test) were produced by EARLIER candidates and are historical.
- Do not invent test results, CLI behaviour or file contents. If something needed is not embedded, say so
  explicitly and list it under "Missing material".
- The machine's profile path and account name are replaced in everything embedded below by <home> and
  <user>, and CRLF line endings are normalised to LF; those are the only edits made to any embedded
  text.  A file that is RAW-BYTE identical to the version you verified last round and that you did not ask
  for is NOT repeated: the manifest entry (mode `unchanged_since_verified_snapshot`) carries its sha256, which
  equals the entry in the embedded snapshot MANIFEST.json.  Every file your round-5 review named as missing
  material IS embedded in full.
- This round's verification_manifest.json cannot be embedded: it records the hash of this very prompt. The
  embedded manifest is the previous round's; the modes this round uses are defined by the builder source
  (FULL, ALWAYS_FULL, DIFF_ONLY, GLOB_FULL, GLOB_LISTED, LISTED) which IS embedded."""

CONTEXT = """=== WHAT THIS IS ===
Round 1 reviewed the first remediation (F1-F8, N1-N15). Round 2 verified the second (V1-V15), round 3 the
third (R1-R13), round 4 the fourth (S1-S15). Round 5 verified the fifth: no Critical finding; S7/S9/S11/S15
CLOSED, S5 NOT CLOSED, the other ten S findings PARTIALLY CLOSED; R13 NOT CLOSED; the cross-cutting claims
A, B, D CONTRADICTED; and new findings T1-T12 (2 High: an abandoned reader callback writing stale state, and
smoke-oracle substring matching of shell text). YOU HAVE NO MEMORY of any earlier call. The round-5 review is
embedded verbatim (docs/gpt_verification/gpt_verification_*.md, newest) and is the authoritative statement of
what each T-finding meant; it also restates the S/R/V/N/F verdicts. The human then authorised: "yes
remediate T1-T12". This prompt embeds the CURRENT code; a SOURCE DIFF block (a few lines of context) shows the
bridge/ changes since the state you verified last round, docs/remediation6_stat.txt lists every changed file,
and the bodies you named as missing are embedded in full. Nothing was registered, activated, committed or
published; v1 is untouched.

Standing constraints for the implementer in this pass (verbatim): "Modify only the isolated v2 candidate."
"Keep v1 files, environment, session records and MCP registration untouched." "Do not run live model calls
without fresh explicit authorization." "Implementing a conservative candidate is not approval to activate it."
D1, D3 and D8 remain human decisions."""

CLAIMS = """=== IMPLEMENTER CLAIMS TO FALSIFY (locations are where the change lives) ===
Cross-cutting design claims (your A-D, restated after the change) - attack these too:
  (A) A process fact is made durable before any dependent mirror is written, on EVERY path (stale conversion,
      ordinary turn, conflicting records), and a disposition command never removes it.
  (B) Process uncertainty and thread disposition are independent durable facts, and NO late writer (an
      abandoned reader callback included) can erase or recreate either: observers merge into a record only
      if it still belongs to their call (Registry.update_marker_if), only while their call's fence is open.
  (C) System tools fail closed; executable selection validates targets and derived launcher artifacts.
  (D) Evidence is complete only if the reader reached EOF, the events object is a frozen snapshot, and every
      returned container was scanned with the full encoded signature in view.

T1  Abandoned observers. Claim: service.py::_CallFence, observe(), on_started/on_thread (merge-only,
    compare-and-merge via state.py::Registry.update_marker_if under markers.lock; fence closed before any
    recovery write); runner.py::Events (lock-serialised mutators, feed calls the observer AFTER releasing the
    lock, finish()/seal() freeze). Tests: T1_*. Look for: a write path that still persists a whole stale
    record from another thread, the fence/lock ordering, writes that bypass markers.lock, a way for finish()
    to be skipped.
T2  Shell semantics. Claim: tools/smoke_oracles.py::tokenize (ONE simple command; separators, pipelines,
    sub-expressions, blocks, variables, comments, line breaks rejected, quoted strings opaque),
    writes_target, is_git_commit, is_rev_parse_head, _js_operation (known tool names only, reserved result
    names rejected, mandatory commas), REFUSAL_RE (specific denial wording only), _refused. Tests: T2_*.
    Look for: a shape the tokenizer accepts that executes more than one thing, a write verb matched on a
    non-target operand, and an attributable denial that could belong to something else.
T3  Purity is validated. Claim: state.py::conflicting_persistence_record/is_pure_thread_debt;
    admin.py::_settle_unbound_thread/cmd_reconcile; service.py::_check_session_health (conflicting record ->
    uncertain, persisted or refused). Tests: T3_*.
T4  Ordinary-turn ordering. Claim: service.py::_locked_call (uncertainty_durable gates the pending mirror;
    completion/observer/final writes are checked and reported). Tests: T4_*.
T5  Carry window. Claim: runner.py::_OVERSIZE_WINDOW = 8192 holds the longest ENCODED signature (13 + 1024
    + 1 characters, 6 bytes each); README now says ASCII \\u00XX and the five simple escapes only. Tests: T5_*.
T6  Generic exceptions. Claim: service.py::ArchitectService.execute attaches warnings to the wrapped error.
T7  Observed thread. Claim: runner.py::turn_problems(result, expected_thread) requires a well-formed
    observed id (and the requested one on resume); tools/send_verification.py::review_is_valid and
    apply_patch_attack_test.py::stage_b use it. Tests: T7_*.
T8  Reconstructed chain. Claim: tools/prefix_logic.py (AST identities, classify_errors) and
    make_verification_prompt.py::prefix_problems/log_problems: module in the overlay, COMPLETE overlay,
    snapshot bytes rehashed, identities and kinds reconstructed, per-test lines unique and paired. Tests: T8_*.
T9  JavaScript whitespace. Claim: apply_patch_attack_test.py::JS_WHITESPACE (space, tab, CR, LF only) in every
    grammar regex and strip; non-ASCII whitespace is conservatively INCONCLUSIVE. Tests: T9_*.
T10 Witnesses. Claim: state.py::Registry._write (first-write backup mandatory and BEFORE the commit; a
    `registry_established` journal event), recovery advice without instance.json. Tests: T10_*.
T11 Raw-byte omission. Claim: make_verification_prompt.py::assemble. Tests: T11_*.
T12 Outcome label. Claim: record_evidence.py::cmd_tests (INVALID = provenance broken, FAILED = tests
    failed). Tests: T12_*.

Changed that you did not ask for (check as potential regressions): every marker write/clear now takes
markers.lock (5 s) and `clear_marker` raises OSError when it cannot; the first registry write now FAILS
(rather than silently succeeding) when its backup cannot be written; `registry_established` is journalled;
runner.py's Events object is frozen after run_turn (anything that mutated it later no longer can);
smoke_oracles accepts far fewer shapes; Stage B fails a turn whose thread id was not observed."""

LIMITS = """=== LIMITS THE IMPLEMENTER ALREADY DECLARES (check that they are complete and not understated) ===
1. No live model call, live attack stage, smoke test or MCP stdio run exists for this source hash.
2. MCP containment is a configuration snapshot plus a narrowed race; no supported deny-all exists
   (the probe shows only that `-c mcp_servers={}` did not disable the listed servers).
3. The attack oracle is wrapper-attested; Codex persists no nested apply_patch records in the ONE historical
   rollout inspected. The grammar is strict: unusual genuine wrappers are INCONCLUSIVE.
4. Commands started by the Windows sandbox service are outside the job and unobservable; the conservative
   default requires operator reconciliation after a timeout/cancel and after a bridge death.
5. Live --json shapes for failed commands were not re-captured; fixtures derive from rollout records.
6. web_search="disabled" has no readout; runtime removal of delegation/image tools is unverified.
7. Repository consistency is bounded by Git's view: same-size/same-mtime untracked edits, ignored files,
   index visibility flags and submodule content are not covered.
8. Executables come from the user's own PATH outside the project; hard links, DLL search order, check-then-use
   replacement and whatever the npm launcher's JavaScript goes on to execute are not controlled.
9. The staged copy isolates the executed source, not the environment; its read-only attribute is a file
   attribute and the post-run fingerprint detects a PERSISTENT difference at that endpoint (not a rewrite
   followed by a restore).
10. Evidence is implementer-executed and bound to a source hash; it was not produced by a second party.
11. The attributing smoke oracles and the stage-B harness were not run live; real shell-call shapes are
    unobserved, so real runs may be INCONCLUSIVE.
12. `CreateProcess` followed by more than 1024 whitespace characters before `{` is outside the bounded
    signature; the scan decodes ASCII \\u00XX and the five simple escapes, not arbitrary \\uXXXX.
13. Marker serialisation uses an OS file lock with a 5 s timeout; a lock that cannot be taken makes a write
    report failure, never block forever.
14. Unrecognised returned-container key names are not in _OUTPUT_KEYS (declared; the request-key skip is
    positional but the name list is finite)."""

REQUEST = """=== WHAT TO PRODUCE (in this order; be precise and do not repeat embedded text) ===
1. VERDICT TABLE for T1-T12, then S1-S15, R1-R13, V1-V15, N1-N15, P3 and F1-F8 as re-graded: CLOSED /
   PARTIALLY CLOSED / NOT CLOSED / CANNOT VERIFY, each with the file::function or test name relied on and
   the specific remaining work. Distinguish "the code does this" from "a test would catch a regression".
2. NEW FINDINGS, ranked Critical/High/Medium/Low. Pay particular attention to regressions INTRODUCED by this
   round: the markers.lock serialisation (deadlock, timeout behaviour, lock-order against the session and
   registry locks), the fence and compare-and-merge (can a legitimate observer update be lost?), the frozen
   Events object (can a legitimate late event be dropped? is `finish` always reached?), the mandatory
   first-write backup, the tokenizer grammar (accepts too much / too little), the reconstructed prefix
   chain (false refusals, AST edge cases). For each: location, concrete failure scenario, why existing tests
   would not catch it, minimal fix.
3. TEST ADEQUACY: use docs/evidence/prefix_check.json (per-test outcome AND failure kind) and the tests:
   which T tests would fail on the pre-fix code behaviourally, which only because an API is missing, which
   pass by declared design, and any that pass for the wrong reason.
4. EVIDENCE: are the classes and wording honest? Is the staged/read-only/post-run chain, with the stated
   detection limits, sufficient provenance for an implementer-executed result? Are resolved / requested /
   uncontained correct?
5. DECISIONS: D1 (including `--ignore-user-config`), D3, D8-web, D8-delegation/image, P2, P3 - options and a
   recommendation (you cannot approve). State which findings should gate the human-authorised live checks in
   REVIEW_PACKAGE.md section 11.
6. CHEAPEST NEXT CHECKS in priority order (mocked first, then human-authorised live), and what each settles.
7. MISSING MATERIAL: exactly what you would need but was not embedded."""


# --------------------------------------------------------------------------- assembly
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _embed_text(data: bytes) -> tuple[str, bool]:
    """Decode for embedding with the machine's profile path / account name replaced.  Returns
    (text, scrubbed).  The per-file sha256 in the manifest stays the hash of the real bytes."""
    from tools.privacy import scrub_local

    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")   # LF only: no CRLF diff noise
    clean = scrub_local(text)
    return clean, clean != text


def expand_full(root: Path) -> list[str]:
    """FULL plus every GLOB_FULL match (sorted, de-duplicated, order preserved)."""
    out: list[str] = []
    seen: set[str] = set()
    for rel in FULL:
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    for pattern in GLOB_FULL:
        matches = [p for p in sorted(root.glob(pattern)) if p.is_file()]
        if pattern in NEWEST_ONLY and matches:
            matches = matches[-1:]            # earlier rounds' reviews are history, not input
        for path in matches:
            rel = path.relative_to(root).as_posix()
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def _source_diff(root: Path, snap: Path) -> str:
    """One unified diff over every bridge/ .py file that differs from the snapshot."""
    names: set[str] = set()
    for base in (root, snap):
        for prefix in SRC_DIFF_PREFIXES:
            d = base / prefix.rstrip("/")
            if d.is_dir():
                names.update(p.relative_to(base).as_posix() for p in d.rglob("*.py"))
    chunks: list[str] = []
    for rel in sorted(names):
        new_p, old_p = root / rel, snap / rel
        new = _embed_text(new_p.read_bytes())[0].splitlines(keepends=True) if new_p.is_file() else []
        old = _embed_text(old_p.read_bytes())[0].splitlines(keepends=True) if old_p.is_file() else []
        if new != old:
            chunks.extend(difflib.unified_diff(old, new, f"verified_last_round/{rel}", f"now/{rel}",
                                               n=DIFF_CONTEXT))
    return "".join(chunks)


def assemble(root: Path = ROOT, snapshot: str = SNAPSHOT) -> tuple[str, dict]:
    """Build (prompt, manifest).  Raises FileNotFoundError when a required file is missing."""
    parts: list[str] = [INSTRUCTIONS, "\n\n", CONTEXT, "\n\n", CLAIMS, "\n\n", LIMITS, "\n\n", REQUEST]
    files: list[dict] = []
    snap = root / snapshot
    for rel in expand_full(root):
        path = root / rel
        data = path.read_bytes()                      # FileNotFoundError is the intended failure
        old_path = snap / rel
        if (rel in FULL and rel not in ALWAYS_FULL and old_path.is_file()
                and old_path.read_bytes() == data):
            # RAW-byte identical to what GPT verified last round (T11: normalised text is not enough)
            files.append({"path": rel, "mode": "unchanged_since_verified_snapshot", "bytes": len(data),
                          "sha256": _sha256(data)})
            continue
        text, scrubbed = _embed_text(data)
        files.append({"path": rel, "mode": "full", "bytes": len(data), "lines": text.count("\n") + 1,
                      "sha256": _sha256(data), "scrubbed_local_identifiers": scrubbed})
        parts.append(f"\n\n=== FILE: {rel} ===\n{text}\n=== END FILE: {rel} ===")

    for rel in DIFF_ONLY:
        path = root / rel
        data = path.read_bytes()
        old_path = snap / rel
        old = _embed_text(old_path.read_bytes())[0].splitlines(keepends=True) if old_path.is_file() else []
        new = _embed_text(data)[0].splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old, new, f"verified_last_round/{rel}", f"now/{rel}",
                                            n=DIFF_CONTEXT))
        files.append({"path": rel, "mode": "diff_vs_verified_snapshot", "bytes": len(data),
                      "diff_bytes": len(diff.encode("utf-8")), "sha256": _sha256(data)})
        parts.append(f"\n\n=== DIFF (vs the version you verified last round): {rel} ===\n"
                     f"{diff or '(unchanged)'}\n=== END DIFF: {rel} ===")

    src_diff = _source_diff(root, snap)
    files.append({"path": "(computed) bridge/ diff vs verified snapshot", "mode": "computed_diff",
                  "bytes": len(src_diff.encode("utf-8")), "sha256": _sha256(src_diff.encode("utf-8"))})
    parts.append("\n\n=== SOURCE DIFF (bridge/, vs the version you verified last round) ===\n"
                 f"{src_diff or '(no changes)'}\n=== END SOURCE DIFF ===")

    listed = list(LISTED)
    for pattern in GLOB_LISTED:
        listed.extend(p.relative_to(root).as_posix() for p in sorted(root.glob(pattern)) if p.is_file())
    for rel in listed:
        path = root / rel
        if path.is_file():
            data = path.read_bytes()
            files.append({"path": rel, "mode": "listed_only", "bytes": len(data), "sha256": _sha256(data)})
    prompt = "".join(parts)
    encoded = prompt.encode("utf-8")
    manifest = {
        "status": "prepared_not_sent",
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "diff_base_snapshot": snapshot,
        "prompt_chars": len(prompt),
        "prompt_bytes": len(encoded),
        "prompt_sha256": _sha256(encoded),      # of the exact bytes written to verification_prompt.md
        "files": files,
    }
    return prompt, manifest


def main() -> int:
    source_sha = current_source_hash()
    problems = evidence_problems(ROOT, source_sha)
    for pattern in REQUIRED_NONEMPTY_GLOBS:
        if not any(p.is_file() and p.stat().st_size for p in ROOT.glob(pattern)):
            problems.append(f"no non-empty file matches {pattern}")
    if problems:
        print("REFUSING to prepare a prompt: the recorded evidence is not bound to the current source.",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("Re-record with tools/record_evidence.py (fingerprint, policy, tests, integrity, cli), then run "
              "this again.", file=sys.stderr)
        return 2
    try:
        prompt, manifest = assemble(ROOT)
    except FileNotFoundError as exc:
        print(f"REFUSING: a required file is missing: {exc.filename}", file=sys.stderr)
        return 2
    manifest["source_combined_sha256"] = source_sha
    out = ROOT / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    # bytes, not text mode: the file must be byte-identical to the prompt that was hashed (no CRLF)
    (out / "verification_prompt.md").write_bytes(prompt.encode("utf-8"))
    (out / "verification_manifest.json").write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    note = "" if manifest["prompt_bytes"] <= SIZE_WARN_BYTES else \
        f"  (WARNING: larger than {SIZE_WARN_BYTES:,} bytes; consider trimming before sending)"
    print(f"prepared (NOT sent): {OUT_DIR.as_posix()}/verification_prompt.md  "
          f"{manifest['prompt_bytes']:,} bytes, {len(manifest['files'])} files, source {source_sha[:16]}...{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
