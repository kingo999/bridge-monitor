"""Prepare (NOT send) the verification prompt for GPT's second-round review.

    python -B tools/make_verification_prompt.py

Builds a self-contained prompt from the current tree and writes
``docs/gpt_verification/verification_prompt.md`` and ``verification_manifest.json``.  This tool never
runs Codex, never uses the subscription allowance, and never sends anything: sending is a separate,
human-authorised step.

It refuses to build when the recorded evidence is not bound to the *current* source hash, when the
recorded test run did not pass, or when a required file is missing, so a prompt can never be prepared
against stale evidence.
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

SNAPSHOT = ".baseline_post_remediation1"      # exactly what GPT reviewed in the first round
OUT_DIR = Path("docs") / "gpt_verification"
SIZE_WARN_BYTES = 650_000

# Embedded in full: everything GPT needs to verify the claims without being told to trust them.
FULL = [
    "README.md",
    "REVIEW_PACKAGE.md",
    "docs/REMEDIATION_CHECKLIST_2.md",
    "docs/gpt_review/gpt_review_20261012T135823Z.md",
    "docs/evidence/source_fingerprint.json",
    "docs/evidence/capability_policy_real_cli.json",
    "docs/remediation2_stat.txt",
    "architect_bridge.py",
    "bridge_admin.py",
    "bridge/__init__.py",
    "bridge/admin.py",
    "bridge/codexcli.py",
    "bridge/config.py",
    "bridge/errors.py",
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
    "tests/fake_codex.py",
    "tests/helpers.py",
    "tests/hard_kill_helper.py",
    "tests/test_review2.py",
    "tests/test_apply_patch_attack.py",
    "tests/test_service.py",
    "tests/test_server_admin.py",
    "tests/test_state.py",
    "tests/test_config_argv_auth.py",
]
# GPT saw these at the snapshot: embed only what changed since.
DIFF_ONLY = ["tests/test_remediation.py"]
# Hash-listed only (no embedded text); available on request.
LISTED = [
    "tools/live_smoke_test.py", "tools/make_telemetry_examples.py", "tools/make_remediation_diff.py",
    "tools/make_v1_diff.py", "tools/v1_manifest.py", "tools/verify_lock.py", "tools/make_lock.py",
    "tools/capture_v1_fixtures.py", "requirements.in", "requirements.lock.txt",
]
EVIDENCE_TESTS_GLOB = "docs/evidence/tests_*.json"


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
        elif rec.get("outcome") != "OK" or rec.get("failing") or not rec.get("tests_run"):
            problems.append(f"{path.name} does not record a passing run")
    return problems


# --------------------------------------------------------------------------- prompt text
INSTRUCTIONS = """You are the GPT ARCHITECT performing a VERIFICATION review of a second remediation of the
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
  recorded, not independently verified; you cannot re-run them. Judge whether the TESTS AS WRITTEN would detect
  the defects you reported (read them), not whether the recorded totals are green.
- No live model call, registration or activation has been made for this source. The only live results that
  exist (smoke test, attack test) were produced by EARLIER candidates and are historical.
- Do not invent test results, CLI behaviour or file contents. If something needed is not embedded, say so
  explicitly and list it under "Missing material"."""

CONTEXT = """=== WHAT THIS IS ===
Round 1: a previous GPT architect call reviewed the first remediation and issued verdicts on F1-F8 (all
PARTIALLY CLOSED) and new findings N1-N15. YOU HAVE NO MEMORY OF THAT CALL: its review is embedded verbatim as
docs/gpt_review/gpt_review_20261012T135823Z.md and is the authoritative statement of what each finding meant.
The human then authorised: remediate N1-N15, remove the `degraded` mode from the production success path (the
P3 recommendation), then prepare this verification. This prompt embeds the CURRENT code in full;
docs/remediation2_stat.txt lists which files changed since the state that was reviewed, and the one test
module that was embedded in round 1 (tests/test_remediation.py) is given as a diff against that version.
Nothing was registered, activated, committed or published; v1 is untouched and still live.

Standing constraints for the implementer in this pass (verbatim): "Modify only the isolated v2 candidate."
"Keep v1 files, environment, session records and MCP registration untouched." "Do not run live model calls
without fresh explicit authorization." "Implementing a conservative candidate is not approval to activate it."
D1, D3 and D8 remain human decisions."""

CLAIMS = """=== IMPLEMENTER CLAIMS TO FALSIFY (one per finding; locations are where the change lives) ===
N1  Host-side git helpers. Claim: every host-side git argv/env is built in bridge/gitsafe.py (--attr-source=<empty
    tree>, core.fsmonitor=false, core.attributesFile=<devnull>, diff.external=, --no-optional-locks, scrubbed
    env; --no-textconv --no-ext-diff on diffs) and used by bridge/fingerprint.py and
    bridge/codexcli.py::_probe_file; git older than 2.40 makes the fingerprint unavailable. Tests:
    tests/test_review2.py::N1_HostGitContainment (a control that proves plain git fires textconv/clean/fsmonitor
    helpers; a mutation test that the control fails when containment is removed; a recursive .git snapshot).
    Declared residual: git still reads $GIT_DIR/info/attributes. Look for: any other host-side git/process route
    that bypasses gitsafe; whether --attr-source really disables filters for `status`; Windows specifics.
N2  State-directory validation. Claim: validate_state_dir runs before every admin state access, including
    quarantine/reinit/restore-prev/end-maintenance, session commands and `status` (a rejected directory is never
    created, locked or probed). bridge/admin.py::_state_only; tests N2_StateDirValidationEverywhere. Look for:
    any remaining path that touches state before validation (server start-up, service, telemetry/journal).
N3  Missing/incomplete ownership. Claim: the child starts CREATE_SUSPENDED, is assigned to a Job Object and only
    then resumed (bridge/jobobject.py, bridge/runner.py::run_turn); failure to attach/resume kills the child
    before any prompt byte is written (ownership_unavailable). After a timeout or cancel on Windows the session
    is in doubt until `reconcile` (P2 conservative default) because sandbox-service-spawned commands are not
    job members. After a normal exit, job accounting lags by ~4 ms, so a survivor is declared only if still
    present after a 0.5 s grace window (EXIT_ACCOUNTING_GRACE_S; found via a flaky test). Tests:
    N3N4_OwnershipAndStaleRecovery, N3_ExitAccountingLag, tests/test_remediation.py F3 tests.
    Look for: windows where a child runs un-owned, handle/job leaks on the refusal path, whether the grace
    window can hide a real survivor or delay a kill harmfully, P2 coverage of every interruption path.
N4  Runner exceptions and stale recovery. Claim: everything after Popen is exception-safe (runner_failed marks the
    session uncertain and kills the child); a stale in-flight marker is auto-cleared only when the recorded
    owner (pid AND process creation time) is verifiably dead AND job ownership was established AND the phase was
    not completed_unbound; otherwise stale_inflight_owner_alive / stale_inflight_unowned / pending_persistence.
    A process that exists but cannot be queried (access denied) counts as alive (found while preparing this
    package: an earlier draft read a failed query as death); an exited process is dead even if a handle
    to it is still open.
    bridge/service.py::_check_session_health, bridge/state.py::process_identity/owner_is_alive.
    Look for: PID reuse, identity-string weaknesses, access-denied handling, a path that still auto-clears.
N5  Maintenance exclusivity. Claim: maintenance is an OS lock plus an owner-stamped flag held for the whole
    lifecycle; a second owner gets maintenance_busy; only the owner clears its flag; session commands and calls
    refuse during maintenance; stale flags need `end-maintenance --yes`. bridge/state.py
    begin_maintenance/end_maintenance/end_stale_maintenance/assert_not_in_maintenance; bridge/admin.py. Tests use
    real threads and a barrier. Look for: lock-ordering deadlocks, TOCTOU between admission and mutation.
N6  Durable admission and persistence. Claim: the in-flight marker must be written before inference
    (inflight_marker_unwritable); it is retained (phase completed_unbound) until the pointer is committed or a
    pending/uncertain marker replaces it; an instance-file write failure becomes success_partial_persistence;
    banners are independent flags and the "pointer already references" claim needs a fresh read.
    bridge/service.py::_locked_call, bridge/state.py::write_marker/update_marker/bind_thread/_ensure_instance.
    Look for: any remaining crash window that allows a silent replacement thread; contradictory banners.
N7  Infrastructure evidence. Claim: pre-completion `error` events and the FULL text of failed command items are
    classified; oversized lines are counted and scanned head+tail (oversize_signature_lines); repo-access
    signatures extended; ordinary non-zero exits and prose are not flagged. bridge/runner.py::classify_item,
    Events.feed, feed_oversize, signature_kinds; bridge/service.py::_verify. Look for: signature gaps, items
    that carry output in fields not gathered, false positives that would withhold valid answers.
N8  Capability drift. Claim: allowed servers come from the FINAL readout; the MCP set is re-read immediately
    before exec (mcp_changed_before_exec) and after the turn (mcp_config_changed_during_call); duplicate
    feature rows are rejected; allowed servers are `uncontained`; resolved evidence is labelled
    snapshot-scoped. The residual race (no supported MCP deny-all exists in CLI 0.162.1) is DOCUMENTED, not
    closed. bridge/codexcli.py::capability_audit/recheck_mcp/parse_features. Look for: whether the recheck
    sits after all other pre-exec work; whether the documented limit is stated honestly.
N9  Oracle trusts wrapper output. Claim: Codex persists no nested apply_patch records on the installed CLI
    (observed), so the oracle can only be WRAPPER-ATTESTED: static wrapper checks (single tools.apply_patch
    call, no throw/Error/literal rejection text), exact comparison of the attempted patch with the expected
    patch, specific policy rejection text, per-turn counts and thread identity; evidence is labelled
    wrapper_attested. tools/apply_patch_attack_test.py::wrapper_analysis/evaluate_turn. Look for: forged
    wrappers that still pass; whether the label and the README wording are honest.
N10 Instance identity. Claim: instance state is absent/unreadable/invalid/ok and every non-ok combination with
    a missing registry fails closed; registry identity must be a string and match; restore_prev validates
    schema and identity. bridge/state.py::instance_state/_read/restore_prev; tests N10_InstanceIdentity.
N11 Fingerprint completeness. Claim: staged and worktree diffs are hashed separately (unborn-safe),
    --untracked-files=all status is capped at 8 MiB and truncation/incomplete reads make the fingerprint
    unavailable, never comparable. bridge/fingerprint.py. Declared blind spot: same-size/same-mtime edits of
    untracked files.
N12 Oracle strength. Claim: generic write failures (disk full, bad path) are never conclusive: OS_DENIAL_RE no
    longer contains "Failed to write file", and the model-free stage A (judge_os_attack) passes a hunk only
    when the engine exited non-zero without timing out, the output names the attacked path AND carries an OS
    access-denial code, and nothing changed. Stage A attacks each hunk in its own run; timeout, launch failure
    and an unattributable failure are INCONCLUSIVE. tools/apply_patch_attack_test.py::classify_output/
    judge_os_attack/stage_a; tests/test_apply_patch_attack.py (StageAAttribution). Look for: whether the
    patterns could reject genuine denials or accept non-denials. NOTE: no live attack run exists for this
    source; if the real engine's message carries no recognised denial code, stage A will report
    INCONCLUSIVE (conservative) rather than PASS.
N13 Preflight file name. Claim: the readability probe uses `findstr` with the name as data, excludes names cmd.exe
    would reparse, prefers source extensions, is described as a tracked-file readability probe, and a skipped
    probe is bannered. bridge/codexcli.py::_probe_file/sandbox_preflight; tests N13N15_SourceProbe.
N14 Identifier ingress. Claim: thread ids are validated at event ingress (bridge/ids.py, runner Events.feed)
    before callbacks/journal/telemetry/banners; nested telemetry strings must be opaque tokens or fixed formats
    (bridge/telemetry.py::sanitise). bridge/redact.py (now embedded) is best-effort SECRET-pattern redaction
    only; excerpts in error text to the caller may contain paths or source and never go to telemetry.
N15 Hidden degradations. Claim: an unavailable fingerprint and a skipped probe are returned in the answer as a
    flag line plus detail, and recorded as warnings. bridge/service.py; tests N11N15_*, N13N15_*.
P3  Claim: `degraded` is removed; GPT_ARCHITECT_ON_TURN_INFRA_FAILURE accepts only `error`; an answer whose
    actual turn hit infrastructure failures is withheld; warnings that do not withhold are prepended as a
    flag line. bridge/config.py, bridge/service.py.
§3  Claim: wording corrected (bounded claim; "implementer-executed" instead of "independently verified";
    rollback wording distinguishes registration from evaluation calls; section references).

Also changed that you did not ask for (check these as potential regressions): suspended process start and
exit-accounting grace; _run_capture reads its cap at call time; containment check in classify_item now uses a
path-boundary match; spawn_failed is now treated as "nothing ran" (it previously marked the session
uncertain); process_identity/owner_is_alive no longer read a failed or access-denied query as owner death;
registry_identity_unverifiable has actionable recovery text; tools/make_telemetry_examples.py rewritten
(degraded example removed); evidence class renamed implementer_executed; diff tools skip every .baseline_*
snapshot."""

LIMITS = """=== LIMITS THE IMPLEMENTER ALREADY DECLARES (check that they are complete and not understated) ===
1. No live model call, live attack stage, smoke test, or MCP stdio run exists for this source hash.
2. MCP containment is a configuration snapshot plus a narrowed race; no supported deny-all exists.
3. The attack oracle is wrapper-attested; Codex persists no nested apply_patch records.
4. Commands started by the Windows sandbox service are outside the job and unobservable; the conservative
   default requires operator reconciliation after a timeout/cancel on Windows.
5. Live --json shapes for failed commands were not re-captured; fixtures derive from rollout records.
6. web_search="disabled" has no readout; runtime removal of delegation/image tools is unverified.
7. Same-size/same-mtime edits to untracked files are invisible to the fingerprint.
8. The mutation check exists for N1 only; other tests were not mutation-tested.
9. Sandbox preflight is readiness, not proof; preflight and the real turn can differ.
10. Evidence is implementer-executed and bound to a source hash; it was not produced by a second party."""

REQUEST = """=== WHAT TO PRODUCE (in this order; be precise and do not repeat embedded text) ===
1. VERDICT TABLE for N1-N15, P3 and F1-F8: CLOSED / PARTIALLY CLOSED / NOT CLOSED / CANNOT VERIFY, each with
   the file::function or test name you relied on, and the specific remaining work. Distinguish "the code does
   this" from "a test would catch a regression".
2. NEW FINDINGS, ranked Critical/High/Medium/Low. Pay particular attention to regressions INTRODUCED by this
   remediation (suspended start/resume handshake, maintenance lock, durable admission, gitsafe, findstr probe,
   exit-accounting grace, the flag-line banners). For each: location, concrete failure scenario (inputs/state ->
   wrong behaviour), why existing tests would not catch it, minimal fix.
3. TEST ADEQUACY: for each of N1-N15, would the named tests fail on the pre-fix code (reason from the embedded
   diff of tests/test_remediation.py and the new test modules)? Name any test that passes for the wrong reason.
4. EVIDENCE: are the evidence classes honest? Is the bounded read-only claim, as now worded in README.md and
   REVIEW_PACKAGE.md, correctly bounded? Are `resolved` / `requested` / `uncontained` correct?
5. DECISIONS: D1, D3, D8-web, D8-delegation/image, P2, P3 - options and a recommendation (you cannot approve).
   State which, if any, of your findings should gate the human-authorised live checks in REVIEW_PACKAGE.md
   section 10.
6. CHEAPEST NEXT CHECKS in priority order (mocked first, then human-authorised live), and what each would settle.
7. MISSING MATERIAL: exactly what you would need but was not embedded."""


# --------------------------------------------------------------------------- assembly
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def assemble(root: Path = ROOT, snapshot: str = SNAPSHOT) -> tuple[str, dict]:
    """Build (prompt, manifest).  Raises FileNotFoundError when a required file is missing."""
    parts: list[str] = [INSTRUCTIONS, "\n\n", CONTEXT, "\n\n", CLAIMS, "\n\n", LIMITS, "\n\n", REQUEST]
    files: list[dict] = []
    recorded_tests = sorted(p.relative_to(root).as_posix() for p in root.glob(EVIDENCE_TESTS_GLOB))
    for rel in [*FULL, *recorded_tests]:
        path = root / rel
        data = path.read_bytes()                      # FileNotFoundError is the intended failure
        text = data.decode("utf-8", errors="replace")
        files.append({"path": rel, "mode": "full", "bytes": len(data), "lines": text.count("\n") + 1,
                      "sha256": _sha256(data)})
        parts.append(f"\n\n=== FILE: {rel} ===\n{text}\n=== END FILE: {rel} ===")

    snap = root / snapshot
    for rel in DIFF_ONLY:
        path = root / rel
        data = path.read_bytes()
        old_path = snap / rel
        old = _read_text(old_path).splitlines(keepends=True) if old_path.is_file() else []
        new = data.decode("utf-8", errors="replace").splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old, new, f"reviewed/{rel}", f"now/{rel}"))
        files.append({"path": rel, "mode": "diff_vs_reviewed_snapshot", "bytes": len(data),
                      "diff_bytes": len(diff.encode("utf-8")), "sha256": _sha256(data)})
        parts.append(f"\n\n=== DIFF (vs the version you reviewed): {rel} ===\n{diff}\n=== END DIFF: {rel} ===")

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
    if problems:
        print("REFUSING to prepare a prompt: the recorded evidence is not bound to the current source.",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("Re-record with tools/record_evidence.py (fingerprint, policy, tests), then run this again.",
              file=sys.stderr)
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
