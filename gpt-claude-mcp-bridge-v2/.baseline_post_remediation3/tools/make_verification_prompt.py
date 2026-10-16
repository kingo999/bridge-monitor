"""Prepare (NOT send) the verification prompt for GPT's third-round review.

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

SNAPSHOT = ".baseline_post_remediation2"      # exactly the state GPT verified in the previous round
OUT_DIR = Path("docs") / "gpt_verification"
SIZE_WARN_BYTES = 760_000

# Embedded in full: everything GPT needs to verify the claims without being told to trust them.
FULL = [
    "README.md",
    "REVIEW_PACKAGE.md",
    "docs/REMEDIATION_CHECKLIST_3.md",
    "docs/remediation3_stat.txt",
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
    "tools/live_smoke_test.py",
    "tools/make_v1_diff.py",
    "tests/fake_codex.py",
    "tests/helpers.py",
    "tests/hard_kill_helper.py",
    "tests/test_review3.py",
    "tests/test_remediation.py",
]
#: bridge/ files changed since the verified snapshot are also shown as ONE computed diff
#: (a few lines of context), so newly introduced behaviour is separable from retained code.
SRC_DIFF_PREFIXES = ("bridge/",)
DIFF_CONTEXT = 1
# Files GPT already saw in full last round: only what changed since (against SNAPSHOT).
DIFF_ONLY = [
    "tests/test_review2.py",
    "tests/test_apply_patch_attack.py",
    "tests/test_service.py",
    "tests/test_server_admin.py",
    "tests/test_state.py",
    "tests/test_config_argv_auth.py",
    "tools/make_telemetry_examples.py",
    "tools/make_remediation_diff.py",
]
# Embedded in full, matched by pattern (the set varies by run).
GLOB_FULL = (
    "tests/fixtures/*.json",
    "docs/evidence/source_fingerprint.json",
    "docs/evidence/capability_policy_real_cli.json",
    "docs/evidence/tests_*.json",
    "docs/evidence/tests_*.log",
    "docs/evidence/integrity.json",
    "docs/evidence/cli_observations.json",
    "docs/gpt_verification/gpt_verification_*.md",
    "docs/gpt_verification/request_*.json",
    "docs/gpt_verification/verification_manifest_round*.json",
)
# Hash-listed only (no embedded text); available on request.
LISTED = [
    "tools/v1_manifest.py", "tools/verify_lock.py", "tools/make_lock.py", "tools/capture_v1_fixtures.py",
    "requirements.in", "requirements.lock.txt", "docs/remediation_stat.txt", "docs/remediation2_stat.txt",
    "docs/remediation2.diff", "docs/telemetry_examples_remediation.jsonl",
]
EVIDENCE_TESTS_GLOB = "docs/evidence/tests_*.json"
REQUIRED_NONEMPTY_GLOBS = ("docs/evidence/tests_*.log", "docs/gpt_verification/gpt_verification_*.md")


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
    return problems


# --------------------------------------------------------------------------- prompt text
INSTRUCTIONS = """You are the GPT ARCHITECT performing a VERIFICATION review of a third remediation of the
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
  recorded, not independently verified; you cannot re-run them. The raw test log and before/after source
  fingerprints are embedded so you can judge provenance, but judge whether the TESTS AS WRITTEN would detect
  the defects, not whether the recorded totals are green.
- No live model call, registration or activation has been made for THIS source. The only live results that
  exist (smoke test, attack test) were produced by EARLIER candidates and are historical.
- Do not invent test results, CLI behaviour or file contents. If something needed is not embedded, say so
  explicitly and list it under "Missing material".
- The machine's profile path and account name are replaced in everything embedded below by <home> and
  <user>, and CRLF line endings are normalised to LF; those are the only edits made to any embedded
  text."""

CONTEXT = """=== WHAT THIS IS ===
Round 1: a previous GPT architect call reviewed the first remediation (F1-F8, new findings N1-N15).
Round 2: a verification call reviewed the second remediation and issued V1-V15 (7 High). YOU HAVE NO MEMORY
of either call. The round-2 review is embedded verbatim (docs/gpt_verification/gpt_verification_*.md) and is
the authoritative statement of what each V-finding meant; it also restates the N verdicts. The human then
authorised: "remediate V1-V15". This prompt embeds the CURRENT code in full; a SOURCE DIFF block (a few
lines of context) shows the bridge/ changes since the state you verified last round (tools/ files are embedded in full),
docs/remediation3_stat.txt lists every changed file, and the test modules you already saw in full are given
as diffs against that state.
Nothing was registered, activated, committed or published; v1 is untouched and still live.

Standing constraints for the implementer in this pass (verbatim): "Modify only the isolated v2 candidate."
"Keep v1 files, environment, session records and MCP registration untouched." "Do not run live model calls
without fresh explicit authorization." "Implementing a conservative candidate is not approval to activate it."
D1, D3 and D8 remain human decisions."""

CLAIMS = """=== IMPLEMENTER CLAIMS TO FALSIFY (one per finding; locations are where the change lives) ===
Two design decisions cut across several findings - attack them too:
  (A) NO stale in-flight record is ever auto-recovered. A dead owner leaves the call `uncertain`; only the
      operator's `reconcile` clears it. (Job closure proves only that job MEMBERS are gone; commands started
      by the Windows sandbox service are outside the job, and nothing durable proves the turn did not
      complete.) Process uncertainty and thread disposition are two facts with two resolutions:
      `reconcile` confirms processes; `adopt`/`reset` decide a thread.
  (B) Executable selection is its own boundary: one resolver (bridge/execpath.py) never searches the current
      directory or the reviewed tree, and is used for git, codex, node, taskkill and tasklist.

V1  Runner cleanup. Claim: bridge/runner.py::run_turn puts everything after Popen (including
    ProcessOwnership()/Events() construction and thread start) inside the cleanup scope; only threads whose
    start() succeeded are joined (readers first, prompt writer last); owner.close() is in an outermost finally;
    the job is terminated on every non-clean exit even if the root already exited; the original categorised
    error is preserved. Tests: tests/test_review3.py::V1_RunnerCleanup (real child, real job).
    Look for: any remaining path where close() is skipped or a join can raise; a window where the prompt could
    be written before ownership; whether terminate-on-exception can kill something it should not.
V2  Recovery deleting the only record. Claim: bridge/service.py::_check_session_health writes the pending marker
    and checks the result BEFORE removing the in-flight record (a failed write keeps it, as
    persistence_failed, and later retries the conversion); a dead owner with a running phase becomes
    `uncertain` (+ a pending marker for a fresh observed thread), never recovered. Tests:
    V2V3_StaleRecoveryNeverDeletesTheOnlyRecord. Look for: any other place that removes an in-flight or
    pending marker without a durable replacement; crash points between the marker transitions.
V3  Hard owner death. Claim: same function - job_object ownership no longer permits automatic recovery.
    Tests: test_hard_owner_death_requires_operator_reconciliation_even_when_job_owned; the old auto-reconcile
    test was rewritten. Look for: a path that still starts a turn after an owner died.
V4  Telemetry boundary. Claim: bridge/service.py::_telemetry_dir validates every candidate including the
    fallback and returns None when none is safe (record skipped, content-free stderr line). Tests:
    V4_TelemetryNeverEscapesTheStateBoundary (home == repository). Look for: any other write that is not
    state-validated (journal, locks, markers, registry) when home lies inside the repository.
V5  Forged wrappers. Claim: tools/apply_patch_attack_test.py::parse_wrapper accepts only a strict
    pass-through grammar (string-literal bindings, exactly one awaited tools.apply_patch(literal | bound
    literal | {input: ...}), text(result) printing that call's own result); the patch compared with the
    expected one is DECODED FROM THE CALL'S ARGUMENT; everything else is INCONCLUSIVE; the label stays
    wrapper_attested. Tests: test_apply_patch_attack.py (forged-wrapper negatives incl. `if (false)`),
    tests/test_review3.py. Look for: any wrapper that passes the grammar yet does not perform / report a
    genuine call (call-by-name tricks, getters, shadowing, template literals, unicode escapes, `await` on a
    non-call, line-continuation), and any genuine shape that is wrongly rejected.
V6  Executable selection. Claim: bridge/execpath.py::trusted_which + bridge/gitsafe.py::trusted_git; resolve_cli,
    kill_tree, running_lock_holders use it; the env scrub drops every GIT_* case-insensitively. Tests:
    V6_ExecutableSelection (planted git/codex in repo and cwd; relative PATH entries). Look for: remaining
    bare-name launches (including tools/ and tests/ scaffolding), symlinks/junctions out of an excluded root,
    PATHEXT tricks, the sandboxed `git` in the preflight, DLL search order for the resolved executable.
V7  Infrastructure evidence completeness. Claim: bridge/runner.py::_gather_text traverses every output container
    (skipping only input-ish keys); OversizeScan streams the whole line through the signature scan and
    excludes recognised prose item types by parsing the type from the head. Tests: V7_*. Look for: output-
    bearing keys that are in the skip list, item shapes not covered, false positives that would withhold a
    valid answer, and the conservative unknown-type policy.
V8  Recovery commands. Claim: bridge/admin.py::cmd_reconcile refuses an unsaved-thread record
    (persistence_failed) and converts an uncertain record's unbound thread into a pending marker;
    _settle_unbound_thread lets adopt/reset settle only the thread debt and keeps independent process
    uncertainty; the service no longer overwrites `uncertain` with `persistence_failed`. Tests: V8_*.
V9  Maintenance. Claim: bridge/state.py::begin_maintenance refuses an existing (stale) flag
    (maintenance_stale); end_maintenance releases the lock on every path; admin._require_quiescent cleans
    up when the probe fails. Tests: V9_*.
V10 Capability recheck. Claim: codexcli.recheck_mcp returns the complete snapshot (unexpected,
    allowed_enabled); Audit.note_allowed_enabled records allow-listed servers first seen later; the LAST
    read is after the temp dir, argv, durable marker and journal, and its refusal clears the marker (no false
    uncertainty). Tests: V10_*. The residual race (no deny-all in CLI 0.162.1) is DOCUMENTED, not closed;
    judge whether it is stated honestly.
V11 OS-denial attribution. Claim: judge_os_attack and evaluate_turn require a write-operation failure with a
    denial code naming the attacked path (a read denial or an unrelated target is INCONCLUSIVE); controls
    A1/A2 verify the update and delete effects. No live run exists for this source: if the real engine's text
    carries no recognised denial code, stage A reports INCONCLUSIVE.
V12 Fingerprint read completeness. Claim: bridge/fingerprint.py::_run_capture records reader exceptions;
    _read_head reports UNBORN only when verified (symbolic ref with a missing target), else unavailable.
    Tests: V12_*.
V13 Evidence binding. Claim: tools/record_evidence.py::cmd_tests fingerprints before AND after, rejects a
    change/nonzero exit/skips, retains the raw log (hash recorded); evidence_problems (this tool) checks all
    of it; `integrity` and `cli` subcommands retain the v1/lock output and model-free CLI help/schema
    observations (including the mcp deny-all probe and a rollout structure summary). Tests: V13_*.
    Look for: remaining ways a result can be attached to code it did not run on.
V14 Warning visibility. Claim: ctx.warn(code, banner) pairs every caller-relevant warning with text; any
    warning without its own text is rendered generically; details are placed BEFORE the answer; README
    corrected. Tests: V14_*.
V15 Exact formats. Claim: fullmatch for every identifier/name validator (ids, telemetry, config, admin,
    codexcli server names, state); typed telemetry fields are checked against their own enum/format.
    Tests: V15_*.

Also changed that you did not ask for (check as potential regressions): suspended-start and exit-accounting
grace (earlier round); `HEAD^{commit}` in the HEAD read; the uncertain marker is now reported before a pending
marker; persistence_failed converts to a pending marker when possible; resolve_cli no longer uses shutil.which;
taskkill/tasklist are absolute System32 paths; `GIT_*` env vars are all dropped; record_evidence gained
`integrity` and `cli`; the diff tool gained `--only`; tools/make_telemetry_examples.py gained two refusals.

Relationship to earlier verdicts (so you can re-grade them): N1->V6 (executable selection), N4->V1, N6->V2/V8,
N9->V5, N10 unchanged, N11->V12, N12->V11, N13 unchanged, N14->V15, N15->V14, N8->V10, N5->V9, N7->V7, N2->V4,
N3->V3. N13, N15 and P3 were CLOSED last round; re-check that nothing regressed them."""

LIMITS = """=== LIMITS THE IMPLEMENTER ALREADY DECLARES (check that they are complete and not understated) ===
1. No live model call, live attack stage, smoke test, or MCP stdio run exists for this source hash.
2. MCP containment is a configuration snapshot plus a narrowed race; no supported deny-all exists.
3. The attack oracle is wrapper-attested; Codex persists no nested apply_patch records (see the rollout
   structure summary in docs/evidence/cli_observations.json for one historical live run). The grammar is
   strict, so an unusual genuine wrapper is INCONCLUSIVE.
4. Commands started by the Windows sandbox service are outside the job and unobservable; the conservative
   default requires operator reconciliation after a timeout/cancel and after a bridge death.
5. Live --json shapes for failed commands were not re-captured; fixtures derive from rollout records.
6. web_search="disabled" has no readout; runtime removal of delegation/image tools is unverified.
7. Same-size/same-mtime edits to untracked files are invisible to the fingerprint.
8. Mutation checks exist for the N1 control; every V-test was also run against the pre-fix source (results in
   REVIEW_PACKAGE.md) - other earlier tests were not mutation-tested.
9. Sandbox preflight is readiness, not proof; preflight and the real turn can differ.
10. Executables come from the user's own PATH outside the project; a compromised PATH entry is out of scope.
11. Evidence is implementer-executed and bound to a source hash; it was not produced by a second party."""

REQUEST = """=== WHAT TO PRODUCE (in this order; be precise and do not repeat embedded text) ===
1. VERDICT TABLE for V1-V15, then the earlier N1-N15 and P3 as re-graded, then F1-F8: CLOSED / PARTIALLY CLOSED
   / NOT CLOSED / CANNOT VERIFY, each with the file::function or test name you relied on and the specific
   remaining work. Distinguish "the code does this" from "a test would catch a regression".
2. NEW FINDINGS, ranked Critical/High/Medium/Low. Pay particular attention to regressions INTRODUCED by this
   round: the strict wrapper grammar (soundness AND false rejections), the executable resolver, the
   uncertain/pending/persistence_failed state machine and its admin transitions, runner cleanup ordering, the
   late MCP recheck and its marker clearing, and the details-first banner rendering. For each: location,
   concrete failure scenario (inputs/state -> wrong behaviour), why existing tests would not catch it, minimal
   fix.
3. TEST ADEQUACY: for each V finding, would the named tests fail on the pre-fix code? (The implementer ran
   them against the pre-fix source - see REVIEW_PACKAGE.md - but verify by reading.) Name any test that
   passes for the wrong reason.
4. EVIDENCE: are the evidence classes honest? Is the before/after fingerprint + retained log sufficient
   provenance for an implementer-executed result? Is the bounded read-only claim, as worded in README.md and
   REVIEW_PACKAGE.md, correctly bounded? Are `resolved` / `requested` / `uncontained` correct?
5. DECISIONS: D1, D3, D8-web, D8-delegation/image, P2, P3 - options and a recommendation (you cannot
   approve). State which, if any, of your findings should gate the human-authorised live checks in
   REVIEW_PACKAGE.md section 10.
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
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if path.is_file() and rel not in seen:
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

    for rel in LISTED:
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
