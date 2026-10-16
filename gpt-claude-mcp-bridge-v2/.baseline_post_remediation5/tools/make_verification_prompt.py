"""Prepare (NOT send) the verification prompt for GPT's fifth-round review.

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

SNAPSHOT = ".baseline_post_remediation4"      # exactly the state GPT verified in the previous round
OUT_DIR = Path("docs") / "gpt_verification"
SIZE_WARN_BYTES = 880_000

# Embedded in full: everything GPT needs to verify the claims without being told to trust them.
# A file listed here that is BYTE-IDENTICAL to the snapshot GPT verified last round is not embedded again
# (it is hash-listed in the manifest, mode `unchanged_since_verified_snapshot`) unless it is in
# ALWAYS_FULL: the review asked for those bodies explicitly.
FULL = [
    "README.md",
    "REVIEW_PACKAGE.md",
    "docs/REMEDIATION_CHECKLIST_5.md",
    "docs/remediation5_stat.txt",
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
    "tests/test_review5.py",
    "tests/test_review3.py",
    "tests/test_remediation.py",
    "tests/test_service.py",
    "tests/test_state.py",
    "tests/test_server_admin.py",
    "tests/test_config_argv_auth.py",
    "requirements.lock.txt",
]
ALWAYS_FULL = frozenset({
    "README.md", "REVIEW_PACKAGE.md", "docs/REMEDIATION_CHECKLIST_5.md", "docs/remediation5_stat.txt",
    "tests/test_review5.py",
    # requested in full by the round-4 review (it received only diffs before)
    "tests/test_review3.py", "tests/test_remediation.py", "tests/test_service.py",
})
#: bridge/ files changed since the verified snapshot are also shown as ONE computed diff
#: (a few lines of context), so newly introduced behaviour is separable from retained code.
SRC_DIFF_PREFIXES = ("bridge/",)
DIFF_CONTEXT = 1
# Files GPT already saw in full last round: only what changed since (against SNAPSHOT).
DIFF_ONLY = [
    "tests/test_review4.py",
    "tests/test_review2.py",
    "tests/test_apply_patch_attack.py",
]
# Embedded in full, matched by pattern (the set varies by run).  Review / request files: newest only.
GLOB_FULL = (
    "tests/fixtures/*.json",
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
    ".baseline_post_remediation4/MANIFEST.json",
)
#: Retained raw logs that are hash-listed only (size): the passing run's per-test lines add ~57 KB, and the
#: gate (log_problems) accounts for them one by one.
GLOB_LISTED = ("docs/evidence/tests_*.log",)
NEWEST_ONLY = ("docs/gpt_verification/gpt_verification_*.md", "docs/gpt_verification/request_*.json",
               "docs/gpt_verification/verification_manifest_round*.json")
# Hash-listed only (no embedded text); available on request.
LISTED = [
    "tools/make_lock.py", "tools/capture_v1_fixtures.py", "requirements.in", "docs/remediation_stat.txt",
    "docs/remediation2_stat.txt", "docs/remediation3_stat.txt", "docs/remediation4_stat.txt",
    "docs/remediation4.diff", "docs/telemetry_examples_remediation.jsonl", "docs/v1_baseline_before.json",
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
    # the overlay is re-verified here against the snapshot actually on disk and the live harness
    snap = root / str(rec.get("snapshot") or "")
    try:
        manifest = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if manifest is None:
        out.append("prefix_check: the snapshot named by the record has no readable MANIFEST.json")
    else:
        if manifest.get("combined_sha256_all_files") != rec.get("snapshot_manifest_combined_sha256_all_files"):
            out.append("prefix_check: the snapshot manifest is not the one the check ran against")
        known = {f["path"]: f["sha256"] for f in manifest.get("files", []) if isinstance(f, dict)}
        for entry in rec.get("overlay") or []:
            path, digest, origin = entry.get("path"), entry.get("sha256"), entry.get("origin")
            if origin == "snapshot" and known.get(path) != digest:
                out.append(f"prefix_check: overlay file {path} is not the snapshot's version")
                break
            if origin == "current_tree":
                live = root / str(path)
                if not live.is_file() or hashlib.sha256(live.read_bytes()).hexdigest() != digest:
                    out.append(f"prefix_check: overlay harness file {path} differs from the current tree")
                    break
        if not rec.get("overlay"):
            out.append("prefix_check: the record carries no overlay file list")
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
        headers = len(re.findall(r"^\S+ \([\w.]+\)", text, re.MULTILINE))
        oks = len(re.findall(r"(?:\.\.\. |^)ok$", text, re.MULTILINE))
        if headers != tests_run:
            out.append(f"the retained log lists {headers} per-test headers but the record says {tests_run}")
        if oks != tests_run:
            out.append(f"the retained log has {oks} `ok` test results but the record says {tests_run}")
    if re.search(r"^(FAIL|ERROR):", text, re.MULTILINE):
        out.append("the retained log contains FAIL/ERROR entries")
    if re.search(r"skipped=\d+", text):
        out.append("the retained log reports skipped tests")
    return out


# --------------------------------------------------------------------------- prompt text
INSTRUCTIONS = """You are the GPT ARCHITECT performing a VERIFICATION review of a fifth remediation of the
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
  raw log is hash-listed only; the gate in tools/make_verification_prompt.py::log_problems accounts for
  every per-test line), but judge whether the TESTS AS WRITTEN would detect the defects, not whether the
  recorded totals are green.
- No live model call, registration or activation has been made for THIS source. The only live results that
  exist (smoke test, attack test) were produced by EARLIER candidates and are historical.
- Do not invent test results, CLI behaviour or file contents. If something needed is not embedded, say so
  explicitly and list it under "Missing material".
- The machine's profile path and account name are replaced in everything embedded below by <home> and
  <user>, and CRLF line endings are normalised to LF; those are the only edits made to any embedded
  text.  A file that is byte-identical to the version you verified last round is NOT repeated: the manifest
  entry (mode `unchanged_since_verified_snapshot`) carries its sha256, which equals the entry in the embedded
  snapshot MANIFEST.json."""

CONTEXT = """=== WHAT THIS IS ===
Round 1 reviewed the first remediation (F1-F8, N1-N15). Round 2 verified the second (V1-V15). Round 3
verified the third and issued R1-R13. Round 4 verified the fourth: no Critical finding, R6/R10/R11 CLOSED,
R1/R13 NOT CLOSED, the other eight PARTIALLY CLOSED, the cross-cutting claims A-D CONTRADICTED, and new
findings S1-S15 (5 High). YOU HAVE NO MEMORY of any earlier call. The round-4 review is embedded verbatim
(docs/gpt_verification/gpt_verification_*.md, newest) and is the authoritative statement of what each S-finding
meant; it also restates the R/V/N/F verdicts. The human then authorised: "remediate S1-S15". This prompt embeds
the CURRENT code; a SOURCE DIFF block (a few lines of context) shows the bridge/ changes since the state you
verified last round, docs/remediation5_stat.txt lists every changed file, tests/test_review3.py,
tests/test_remediation.py and tests/test_service.py are embedded IN FULL (you asked for those bodies), the
modules you saw in full last round that changed since are given as diffs against the verified state, and the
verification sender you reviewed as an outside script is now part of the source (tools/send_verification.py).
The diff base's MANIFEST.json and its hash-scope definitions are embedded. Nothing was registered, activated,
committed or published; v1 is untouched.

Standing constraints for the implementer in this pass (verbatim): "Modify only the isolated v2 candidate."
"Keep v1 files, environment, session records and MCP registration untouched." "Do not run live model calls
without fresh explicit authorization." "Implementing a conservative candidate is not approval to activate it."
D1, D3 and D8 remain human decisions."""

CLAIMS = """=== IMPLEMENTER CLAIMS TO FALSIFY (locations are where the change lives) ===
Cross-cutting design claims (your A-D, restated after the change) - attack these too:
  (A) No stale in-flight record is ever cleared by a disposition command; the process fact is made durable
      FIRST and a failed primary write is never followed by a mirrored pending write.
  (B) Process uncertainty and thread disposition are independent facts: one derivation (state.thread_debt),
      and a settlement is a DURABLE field of the record (`thread_settled`), not the absence of a field.
  (C) System tools fail closed; executable selection validates targets and derived launcher artifacts.
  (D) Evidence is complete only if the reader reached EOF and every returned container was scanned.

S1  reset/adopt keep every in-flight record except a pure `persistence_failed` thread debt. Claim:
    admin.py::_settle_unbound_thread, _retained_inflight_note, cmd_reset, cmd_adopt. Tests: S1_*,
    test_review4.py::R1_*test_reset_and_adopt_recognise_a_raw_stale_record_but_keep_the_process_fact (the
    version that required deletion was reversed). Look for: any state/field combination where a process fact
    is still removed by a disposition command; a path that clears `inflight` without `reconcile`.
S2  A settled disposition stays settled. Claim: state.py::thread_debt honours `thread_settled`; the settlement
    is written at reset/adopt time even for `pointer_bound` records. Tests: S2_*. Look for: a thread id for
    which the debt can be derived again (different key, a second call, record rewrites that drop the field:
    whole-record writes in service.py copy `marker` - check that `thread_settled` survives every write).
S3  The primary write is checked. Claim: service.py::_check_session_health (3 attempts; raises
    `uncertainty_marker_unwritable`; no pending write); the runner_failed marker write is retried and warns.
    Tests: S3_*. Look for: the other unchecked write results in service.py (persistence_failed branch, marker
    writes in settle_failed_turn, on_started/on_thread observers).
S4  A reader not at EOF is incomplete evidence. Claim: runner.py::run_turn (alive-after-join and never-EOF ->
    reader_failed; Events.seal), READER_JOIN_TIMEOUT_S, runner.turn_problems. Tests: S4_*. Look for: races
    between the reader thread and RunResult construction, state mutated after seal, join semantics.
S5  Smoke oracles attribute operations. Claim: tools/smoke_oracles.py (Operation, operations, _native_operation,
    _js_operation, _literal_arguments, _writes, _refused). Tests: S5_*. Look for: forgeable shapes that still
    become Operations, verbs that match reads, wrappers whose printed result is not the call's result, and
    genuine real-world shapes wrongly rejected (they stay INCONCLUSIVE, which is the intended failure mode).
S6  Output scanning. Claim: runner.py::_gather_text_ex (request keys skipped only outside returned
    containers; _OUTPUT_KEYS), _JSON_ESCAPE/_unescape_json, window 4096, `CreateProcess\\s{0,1024}\\{`.
    Tests: S6_*. Look for: container names missing from _OUTPUT_KEYS, request-like keys that carry output.
S7  System tools fail closed. Claim: execpath.py::system_tool raises SystemDirectoryUnavailable; callers
    (kill_tree, running_lock_holders) treat it as unavailable. Tests: S7_*.
S8  Wrapper grammar. Claim: tools/apply_patch_attack_test.py::_JS_RESERVED, _STRLIT (no raw CR/LF in quoted
    strings), decode_js_literal (template CRLF -> LF). Tests: S8_*. Look for: JavaScript lexical forms the
    grammar still accepts that cannot execute.
S9  Staged execution is re-fingerprinted. Claim: record_evidence.py::cmd_tests (read-only stage, post-run
    staged fingerprint, parameters root/evidence), run_problems(staged_after). Tests: S9_* (a test that
    rewrites a staged module invalidates the record).
S10 Prefix evidence is a checkable chain. Claim: tools/prefix_check.py (overlay verification, expected count,
    loader errors, classify_errors) and make_verification_prompt.py::prefix_problems/log_problems. Tests: S10_*.
S11 One guarded no-execution cleanup. Claim: service.py::abort_before_exec used by the refused-launch branch
    too; fallback persistence checked and reported (`refusal_marker_not_cleared`); settle_failed_turn's clear
    cannot mask the categorised failure. Tests: S11_*.
S12 Error exits carry warnings. Claim: service.py::ArchitectService._attach_warnings. Tests: S12_*. Look for:
    paths that raise a non-BridgeError or raise after _attach_warnings has run.
S13 One-call registry loss. Claim: state.py::Registry._write (prev written with the FIRST write),
    _journal_proves_establishment (events turn_finished(usable) / thread_adopted / registry_reinitialised /
    registry_restored_prev; `pointer_not_saved` deliberately excluded). Tests: S13_*. Look for: false
    "lost" verdicts for a state directory that never had a registry.
S14 The sender is reviewed source. Claim: tools/send_verification.py::review_is_valid, runner.turn_problems.
    Tests: S14_*.
S15 Exact banners / flag reporting. Claim: service.py persistence_code; admin.py::_end_maintenance_reporting.
    Tests: S15_*.
Also: codexcli.py::Simple.complete (+ version/auth/catalog/features/mcp readouts); the allowed-server union over
both audit readouts; N1's control now requires ALL THREE helper kinds to fire (N1_HostGitContainment.REQUIRED);
the R4 tautology was removed; every launch in tools/ and tests/ uses the absolute trusted git (the N1 control
uses it WITHOUT containment flags); the gate counts per-test lines.

Retained items: N10, V12 unchanged. Evidence wording unchanged from the round-4 package except the
fingerprint record's `environment_evidence_class`.

Also changed that you did not ask for (check as potential regressions): reset/adopt now leave an in-flight
record in place where they used to delete it (an extra operator `reconcile`); `registry.prev.json` now exists
after the first registry write; tests run from a READ-ONLY staged copy; `Simple.complete` can refuse a readout
that previously parsed from a truncated prefix; error messages are longer."""

LIMITS = """=== LIMITS THE IMPLEMENTER ALREADY DECLARES (check that they are complete and not understated) ===
1. No live model call, live attack stage, smoke test or MCP stdio run exists for this source hash.
2. MCP containment is a configuration snapshot plus a narrowed race; no supported deny-all exists
   (the probe shows only that `-c mcp_servers={}` did not disable the listed servers).
3. The attack oracle is wrapper-attested; Codex persists no nested apply_patch records (one historical
   rollout's structure is in docs/evidence/cli_observations.json). The grammar is strict: unusual genuine
   wrappers are INCONCLUSIVE.
4. Commands started by the Windows sandbox service are outside the job and unobservable; the conservative
   default requires operator reconciliation after a timeout/cancel and after a bridge death.
5. Live --json shapes for failed commands were not re-captured; fixtures derive from rollout records.
6. web_search="disabled" has no readout; runtime removal of delegation/image tools is unverified.
7. Repository consistency is bounded by Git's view: same-size/same-mtime untracked edits, ignored files,
   index visibility flags and submodule content are not covered.
8. Executables come from the user's own PATH outside the project; hard links, DLL search order, check-then-use
   replacement and whatever the npm launcher's JavaScript goes on to execute are not controlled.
9. The staged copy isolates the executed source, not the environment (PATH, %TEMP%, the venv, the CLI); its
   read-only attribute is a file attribute, not a sandbox - the post-run fingerprint DETECTS a change.
10. Evidence is implementer-executed and bound to a source hash; it was not produced by a second party.
11. tools/live_smoke_test.py, the rewired smoke oracles' attribution grammar and the stage-B attack harness were
    not run live for this source; the real shapes of recorded shell calls inside the `exec` wrapper have not
    been observed, so real runs may be INCONCLUSIVE until they are.
12. `CreateProcess` followed by more than 1024 whitespace characters before `{` is outside the bounded
    signature (linear-time scanning trade-off).
13. The journal witness for registry establishment is best-effort (the journal itself can be lost)."""

REQUEST = """=== WHAT TO PRODUCE (in this order; be precise and do not repeat embedded text) ===
1. VERDICT TABLE for S1-S15, then R1-R13, V1-V15, N1-N15, P3 and F1-F8 as re-graded: CLOSED / PARTIALLY
   CLOSED / NOT CLOSED / CANNOT VERIFY, each with the file::function or test name relied on and the specific
   remaining work. Distinguish "the code does this" from "a test would catch a regression".
2. NEW FINDINGS, ranked Critical/High/Medium/Low. Pay particular attention to regressions INTRODUCED by this
   round: retained in-flight records after reset/adopt (can a record now block forever, or be resurrected?),
   `thread_settled` surviving every whole-record write, the retry/raise paths in _check_session_health, the
   sealed Events object, the attributing smoke oracles, the stricter evidence gates (false refusals), the
   read-only staged copy, and the always-written registry.prev.json. For each: location, concrete failure
   scenario, why existing tests would not catch it, minimal fix.
3. TEST ADEQUACY: use docs/evidence/prefix_check.json (per-test outcome AND failure kind: assertion /
   api_missing / behavioural_error) and the tests: which S tests would fail on the pre-fix code
   behaviourally, which only because an API is missing, which pass by declared design, and any that pass for
   the wrong reason.
4. EVIDENCE: are the classes and wording honest? Is read-only staging + a post-run staged fingerprint +
   before/after live fingerprints sufficient provenance for an implementer-executed result? Is the bounded
   read-only claim correctly bounded? Are resolved / requested / uncontained correct?
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
                names.update(p.relative_to(base).as_posix() for p in d.glob("*.py"))
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
                and _embed_text(old_path.read_bytes())[0] == _embed_text(data)[0]):
            # byte-identical (after the same LF/scrub normalisation) to what GPT verified last round
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
