> **SUPERSEDED HASH (2026-10-13, later):** the Desktop-coexistence change (log-aware sandbox diagnosis) produced source hash `4ba8cdad...d140`, and later `bb21f493...f942d` (475 tests OK) and **`f984445ae1f0960efdab4e0064c512e278bfc1c95a35f00fde11798132811f4e`** (483 tests OK; adds the state-directory `settings.json` backend override, see section 14), with with the unelevated-backend / harness-ACL changes and live results in `docs/DESKTOP_COEXISTENCE.md` section 12), each with its own re-recorded `docs/evidence/`. This file describes `5228d583...93de`; its records are archived in `docs/history/evidence_final_signoff/`. See `docs/DESKTOP_COEXISTENCE.md`.

# Final sign-off evidence index

Final source hash (`tools/record_evidence.py::fingerprint()`, 56 files):

    5228d5837ef4f1ee3012fd8a79d7fb3212ffb593d16899a0aa0aa06cc7f793de

Every record in `docs/evidence/` was re-recorded for this hash (the round-6 records are archived in
`docs/history/evidence_round6/`). All evidence below is **implementer_executed** unless stated: produced by
Claude Code, not independently reproduced by a second party. This is NOT a GPT verification of the
post-round-6 source (see "What GPT saw").

## Records bound to the final hash

| Record | What it establishes | Result |
|---|---|---|
| `source_fingerprint.json` | the exact source set (bridge/, tools/, tests/, fixtures, top-level scripts, requirements, README) | 56 files |
| `tests_final_signoff_full.json` + `.log` | the whole mocked suite ran from a staged READ-ONLY copy whose fingerprint equals the live tree before AND after the run, and the staged copy again after the run | **455 tests, OK, 0 skipped, 0 problems**, 661.6 s |
| `prefix_check.json` + `.log` | the 34 tests added after round 6 (`tests.test_review7`) run against the round-6 verified source (`.baseline_post_remediation6`) | 30 fail/error (15 assertion, 9 api_missing, 6 behavioural error), 4 pass = the 4 declared regression guards, 0 undeclared, 0 overlay problems |
| `capability_policy_real_cli.json` | effective capability policy read from the installed CLI 0.162.1 (model-free) | unchanged |
| `integrity.json` | v1 files / listing / state / venv UNCHANGED; installed packages match `requirements.lock.txt` (30); `~/.codex/config.toml` stat difference (size 3348 -> 3402, mtime 2026-10-13 08:00:01Z) still present and unchanged since round 4 (content never opened) | v1 untouched |
| `cli_observations.json` | model-free CLI help/schema observations; the historical rollout structure (no separately named nested `apply_patch` records) | recorded |

`docs/remediation7.diff`, `docs/remediation7_src.diff` and `_stat.txt` are the delta against
`.baseline_post_remediation6` (exactly the state GPT verified in round 6): 11 source files, of which the
bridge runtime delta is `admin.py` +37/-11, `errors.py` +5, `runner.py` +15/-1, `service.py` +41/-9,
`state.py` +16/-2; plus `smoke_oracles.py`, `live_smoke_test.py`, `apply_patch_attack_test.py`, README and
the tests.

## Live evidence (not hash-bound records; results are quoted in `docs/POST_ROUND6_DELTA.md`)

Raw output is not retained (it contains home paths). The final live smoke run, 24/24 PASS / 0 FAIL / 0
INCONCLUSIVE (runtime tool inventory NOT ASSESSED), ran with the `bridge/`, `tools/` and `README.md` bytes of
the final hash: the only file that changed after it is `tests/test_review7.py` (made self-contained so the
prefix check can import it; checked by file modification times). Stage A of the attack test: 3 PASS,
0 FAIL, 4 INCONCLUSIVE (the patch engine emits no OS denial code; nothing was written). Earlier live runs
are documented there too.

## What GPT saw

GPT's last review covered the round-6 source (`05f6bd6c...3438`), verdict "defer activation", no Critical
finding. Everything in `remediation7_src.diff` is post-review and unreviewed: U1 (smoke-oracle
attribution), the smoke-harness part of U10, the numeric-option grammar and probe, the `repo_access`
signature narrowing, and U3/U4/U8.

## What this evidence does not establish

* Not independently reproduced; no second-party verification of the post-round-6 delta.
* Open from GPT's round-6 review: U2, U5-U7, U9, U10 (Stage B labels).
* The runtime tool inventory was never obtained; the model's self-report lists `collaboration.*` tools
  although the `multi_agent` gate reads disabled (D8-delegation unresolved).
* The MCP deny-all race, hard links / DLL search order, and service-spawned children are documented, not
  closed. The staged run does not freeze the environment (PATH, `%TEMP%`, venv, CLI) and does not detect a
  rewrite-and-restore of a staged file.
* Decisions D1 / D3 / D8 are the human's. Nothing is registered, activated, committed or published.
