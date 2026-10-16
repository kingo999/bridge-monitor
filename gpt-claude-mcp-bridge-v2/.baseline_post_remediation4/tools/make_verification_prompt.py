"""Prepare (NOT send) the verification prompt for GPT's fourth-round review.

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

SNAPSHOT = ".baseline_post_remediation3"      # exactly the state GPT verified in the previous round
OUT_DIR = Path("docs") / "gpt_verification"
SIZE_WARN_BYTES = 880_000

# Embedded in full: everything GPT needs to verify the claims without being told to trust them.
FULL = [
    "README.md",
    "REVIEW_PACKAGE.md",
    "docs/REMEDIATION_CHECKLIST_4.md",
    "docs/remediation4_stat.txt",
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
    "tests/test_review4.py",
    "tests/test_review2.py",
    "tests/test_apply_patch_attack.py",
    # GPT asked for these unchanged bodies in round 3 (schema, cancellation, lock, argv equivalence)
    "tests/test_state.py",
    "tests/test_server_admin.py",
    "tests/test_config_argv_auth.py",
    "requirements.lock.txt",
]
#: bridge/ files changed since the verified snapshot are also shown as ONE computed diff
#: (a few lines of context), so newly introduced behaviour is separable from retained code.
SRC_DIFF_PREFIXES = ("bridge/",)
DIFF_CONTEXT = 1
# Files GPT already saw in full last round: only what changed since (against SNAPSHOT).
DIFF_ONLY = [
    "tests/test_remediation.py",
    "tests/test_review3.py",
    "tests/test_service.py",
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
    "docs/gpt_verification/send_verification_source.py.txt",
    ".baseline_post_remediation3/MANIFEST.json",
)
#: Retained raw logs that are hash-listed only (size): the passing run's per-test lines add ~57 KB and
#: carry nothing the record and the embedded gate (log_problems) do not already state.
GLOB_LISTED = ("docs/evidence/tests_*.log",)
NEWEST_ONLY = ("docs/gpt_verification/gpt_verification_*.md", "docs/gpt_verification/request_*.json",
               "docs/gpt_verification/verification_manifest_round*.json")
# Hash-listed only (no embedded text); available on request.
LISTED = [
    "tools/make_lock.py", "tools/capture_v1_fixtures.py", "requirements.in", "docs/remediation_stat.txt",
    "docs/remediation2_stat.txt", "docs/remediation3_stat.txt", "docs/remediation3.diff",
    "docs/telemetry_examples_remediation.jsonl", "docs/v1_baseline_before.json",
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
    for name in ("integrity.json", "cli_observations.json", "prefix_check.json"):
        rec = load(ev / name)
        if rec is None:
            problems.append(f"docs/evidence/{name} is missing or unreadable")
        elif rec.get("source_combined_sha256") != source_sha:
            problems.append(f"{name} is bound to a different source hash (stale)")
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
    return problems


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
    if re.search(r"^(FAIL|ERROR):", text, re.MULTILINE):
        out.append("the retained log contains FAIL/ERROR entries")
    if re.search(r"skipped=\d+", text):
        out.append("the retained log reports skipped tests")
    return out


# --------------------------------------------------------------------------- prompt text
INSTRUCTIONS = """You are the GPT ARCHITECT performing a VERIFICATION review of a fourth remediation of the
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
  raw log is hash-listed only; its per-test lines add nothing the record and the gate in
  tools/make_verification_prompt.py::log_problems do not state), but judge whether the TESTS AS WRITTEN would
  detect the defects, not whether the recorded totals are green.
- No live model call, registration or activation has been made for THIS source. The only live results that
  exist (smoke test, attack test) were produced by EARLIER candidates and are historical.
- Do not invent test results, CLI behaviour or file contents. If something needed is not embedded, say so
  explicitly and list it under "Missing material".
- The machine's profile path and account name are replaced in everything embedded below by <home> and
  <user>, and CRLF line endings are normalised to LF; those are the only edits made to any embedded
  text."""

CONTEXT = """=== WHAT THIS IS ===
Round 1: a GPT architect call reviewed the first remediation (F1-F8, N1-N15). Round 2 verified the second
(V1-V15). Round 3 verified the third and issued R1-R13 (4 High) plus corrections to evidence wording and the
retained N10/V12 items. YOU HAVE NO MEMORY of any earlier call. The round-3 review is embedded verbatim
(docs/gpt_verification/gpt_verification_*.md, newest) and is the authoritative statement of what each R-finding
meant; it also restates the V/N/F verdicts. The human then authorised: "remediate R1-R13". This prompt embeds
the CURRENT code in full; a SOURCE DIFF block (a few lines of context) shows the bridge/ changes since the
state you verified last round, docs/remediation4_stat.txt lists every changed file, the test modules you
asked for in full are embedded in full (test_review2, test_apply_patch_attack, test_state,
test_server_admin, test_config_argv_auth; plus the new test_review4), and test_review3, test_remediation
and test_service (which you saw in full last round) are given as diffs against the verified state. The diff base's MANIFEST.json and its hash-scope
definitions are embedded. Nothing was registered, activated, committed or published; v1 is untouched.

Standing constraints for the implementer in this pass (verbatim): "Modify only the isolated v2 candidate."
"Keep v1 files, environment, session records and MCP registration untouched." "Do not run live model calls
without fresh explicit authorization." "Implementing a conservative candidate is not approval to activate it."
D1, D3 and D8 remain human decisions."""

CLAIMS = """=== IMPLEMENTER CLAIMS TO FALSIFY (locations are where the change lives) ===
Cross-cutting design claims - attack these too:
  (A) A dead owner is NEVER auto-recovered, whatever phase it reached. The process fact is made durable
      FIRST (primary in-flight record, whole-record writes); the thread debt is mirrored second.
  (B) Process uncertainty and thread debt are two facts with two resolutions, derived by ONE function
      (state.thread_debt) used by the service and every admin command, including on RAW stale records.
  (C) Executable selection validates the FILE's real target and derived launcher artifacts, not only
      directories; Windows system tools come from GetSystemDirectoryW.
  (D) Evidence that cannot be read completely is never a review (reader failure, traversal limits).

R1  Thread debt on every path. Claim: bridge/state.py::thread_debt; bridge/admin.py::cmd_reconcile,
    _settle_unbound_thread, _bound_thread; service.py (in-doubt marker records unbound_thread for a fresh
    announced thread). Tests: tests/test_review4.py::R1_*. Look for: any path that clears an in-flight record
    without writing the debt first; a state/field combination thread_debt misjudges (resumed, bound pointer).
R2  Durable process fact. Claim: service.py::_check_session_health converts `running` first (primary record,
    then pending); _locked_call writes the WHOLE in-memory record every time (retries the uncertain write
    three times; `uncertainty_marker_unwritable` is a visible warning); a completed phase does not imply
    reconciled processes. Tests: R2_*. Look for: read-modify-write remnants, crash windows between the
    transitions, a path where reconcile makes a call runnable without both facts resolved.
R3  Execution targets. Claim: bridge/execpath.py::trusted_which (target checked), trusted_target,
    system_tool; codexcli.py::resolve_cli validates the shim's node.exe/codex.js (cli_target_untrusted);
    tools/live_smoke_test.py and tests/helpers.py use gitsafe.trusted_git. Tests: R3_*. Look for: links the
    tests only simulate (symlinks need privilege - the test patches realpath), junctions, derived artifacts
    not validated, remaining bare-name launches (tools/, tests/), check-then-use.
R4  Evidence completeness. Claim: runner.py::_gather_text_ex (iterative; limits reported),
    classify_item_ex, OversizeScan (escape-resolving scan, 2048-byte window, anchored item-type regex),
    Events.reader_failed / evidence_truncated_items, service.py `turn_evidence_incomplete`. Tests: R4_*.
    Look for: output-bearing keys still skipped, encodings other than \\uXXXX, a reader failure path that
    is still silent (stderr, stdin), false positives that withhold valid answers.
R5  Wrapper semantics. Claim: tools/apply_patch_attack_test.py::parse_wrapper (program order, reserved and
    reused names), _resolve_arg, patch_matches (complete argument; CRLF/trailing newline only). Tests:
    R5_*, test_apply_patch_attack.py. Look for: JavaScript semantics the grammar still mis-models
    (hoisting of `var`/function, getters, comma/ASI forms, unicode escapes, template literals, `await`
    placement), and genuine shapes wrongly rejected.
R6  Exact target. Claim: names_write_denial parses `Failed to <verb> <PATH>: ... denied` and compares the
    normalised full path under the recorded base (repo_root / base_dir); unparseable => INCONCLUSIVE.
    Tests: R6_*. Look for: path forms the engine really prints (\\?\\, 8.3 names, casing, symlinks).
R7  Maintenance. Claim: state.py::begin_maintenance wraps every post-lock operation; admin commands report
    and exit 1 when the flag could not be removed. Tests: R7_*.
R8  Refusal cleanup. Claim: service.py::abort_before_exec corrects the facts first, never lets a cleanup
    failure mask the refusal, and durably marks `refused_before_exec` (cleared by the next health check).
    Tests: R8_*.
R9  Evidence gate. Claim: record_evidence.py::cmd_tests executes from a STAGED COPY (fingerprint before,
    staged, after); make_verification_prompt.py::evidence_problems validates every embedded record and
    reconciles the summary with the retained log (log_problems); tools/prefix_check.py retains the pre-fix
    overlay procedure and raw output. Tests: R9_*. Look for: what a staged copy still does not isolate
    (shared %TEMP%, the venv, env), and any record embedded without a binding check.
R10 Cleanup gaps. Claim: runner.py::run_turn creates ALL bookkeeping before Popen; _terminate_owned and
    _still_running guard poll(). Tests: R10_*.
R11 Typed telemetry. Claim: telemetry.py::sanitise requires a string before enum membership. Tests: R11_*.
R12 Exact rendered codes. Claim: no prefix exemption; dedicated banners register their exact codes in
    ctx.bannered; everything else is rendered generically. Unexpected servers seen after the audit become
    named uncontained exposures (capabilities.mcp_unexpected_seen). Tests: R12_*.
R13 Smoke oracles. Claim: tools/smoke_oracles.py derives control/attempt/refusal from the recorded rollout;
    an unchanged filesystem is enforcement evidence only when attempt AND refusal are recorded; the
    model's tool list is a labelled self-report (INCONCLUSIVE). Tests: R13_* (pure functions only; the
    harness itself was not run live).
Retained items: N10 (registry.prev.json proves loss even when instance.json is gone: registry_lost);
V12 (UNBORN only when `show-ref --verify` exits 1). Evidence wording: executed model-free commands are
`implementer_executed` (kind model_free_cli_observation); hash scopes are defined in the embedded
MANIFEST.json; the deny-all probe is described as "this override", and `--ignore-user-config` is recorded
as not observable through `mcp list` and NOT adopted (a decision for the human).

Also changed that you did not ask for (check as potential regressions): `reconcile` now converts a derived
debt to a pending marker and can refuse (exit 1); `quarantine` exits 1 when the flag cannot be removed;
`thread_debt` treats an uncertain fresh call with an observed thread as owing a disposition (an extra operator
step after a timeout on a FRESH call); runner traversal limits (64 levels / 200,000 nodes); the recorder
runs tests from a staged copy; `tools/prefix_check.py`, `tools/smoke_oracles.py`, `bridge/execpath.py`
target validation; README section numbers (live checks are section 11)."""

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
8. Executables come from the user's own PATH outside the project; hard links, DLL search order and
   check-then-use replacement are not controlled.
9. The staged copy isolates the executed source, not the environment (PATH, %TEMP%, the venv, the CLI).
10. Evidence is implementer-executed and bound to a source hash; it was not produced by a second party.
11. tools/live_smoke_test.py and the stage-B attack harness were not run live for this source."""

REQUEST = """=== WHAT TO PRODUCE (in this order; be precise and do not repeat embedded text) ===
1. VERDICT TABLE for R1-R13, then V1-V15, N1-N15, P3 and F1-F8 as re-graded: CLOSED / PARTIALLY CLOSED / NOT
   CLOSED / CANNOT VERIFY, each with the file::function or test name relied on and the specific remaining
   work. Distinguish "the code does this" from "a test would catch a regression".
2. NEW FINDINGS, ranked Critical/High/Medium/Low. Pay particular attention to regressions INTRODUCED by this
   round: the thread_debt derivation and the reconcile/adopt/reset transitions, whole-record marker writes,
   the target-validating resolver, the traversal limits and the new `turn_evidence_incomplete` refusal, the
   program-order grammar, exact-path denial parsing, staged execution and the stricter evidence gate. For each:
   location, concrete failure scenario, why existing tests would not catch it, minimal fix.
3. TEST ADEQUACY: use tools/prefix_check.py's embedded output (docs/evidence/prefix_check.json/.log) and read
   the tests: which R tests would fail on the pre-fix code, which pass by declared design, and any that pass
   for the wrong reason.
4. EVIDENCE: are the classes and wording now honest? Is staged execution + before/after fingerprints
   sufficient provenance for an implementer-executed result? Is the bounded read-only claim correctly
   bounded? Are resolved / requested / uncontained correct, including accumulated allowed servers and the
   new unexpected-server exposure?
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
    for rel in expand_full(root):
        path = root / rel
        data = path.read_bytes()                      # FileNotFoundError is the intended failure
        text, scrubbed = _embed_text(data)
        files.append({"path": rel, "mode": "full", "bytes": len(data), "lines": text.count("\n") + 1,
                      "sha256": _sha256(data), "scrubbed_local_identifiers": scrubbed})
        parts.append(f"\n\n=== FILE: {rel} ===\n{text}\n=== END FILE: {rel} ===")

    snap = root / snapshot
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
