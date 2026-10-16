# Remediation checklist 4 (fourth round: GPT verification findings R1-R13)

Trigger: GPT's verification review of the third remediation,
`docs/gpt_verification/gpt_verification_20261013T064429Z.md` (4 High: R1-R4; R5-R13 Medium/Low,
plus corrected evidence wording and the retained N10/V12 items). Authorised by the human:
"remediate R1-R13". Same boundaries: isolated v2 only, no live model calls, no registration,
no activation, no sending (a separate authorisation is required for the round-4 send).
Diff base: `.baseline_post_remediation3/` (exactly the state GPT verified; hashes in its
`MANIFEST.json`, whose `hash_scope` explains why its combined hash differs from the evidence hash).

Process note: as in round 3, the list was written while implementing. Each new test was run
against the frozen snapshot with `tools/prefix_check.py`; 38 of 40 round-4 tests fail on the
pre-fix code and 2 pass by declared design (they pin behaviour that must NOT regress:
`N10...test_a_genuine_first_initialisation_is_still_allowed`,
`R2...test_reset_clears_thread_debt_but_keeps_independent_process_uncertainty`).

## Decisions that shaped several findings

| Decision | Why |
|---|---|
| **One derivation of thread debt** (`state.thread_debt`) used by the service and every admin command (R1). | Round 3 gave `reconcile` and the service different views of a raw stale record, so an observed-but-unbound thread could be forgotten by the one path nobody had converted. |
| **The process fact is durable first, the thread debt second** (R2). A dead owner is `uncertain` whatever phase it reached. | A `completed_unbound` phase proves a thread id was seen, not that sandbox-service commands finished. |
| **Whole-record writes** of the in-flight marker (R2). | A read-modify-write phase update could resurrect or erase the uncertainty written a moment before. |
| **Validate the file's target, not just its directory** (R3). | A link in a trusted PATH directory can point back into the reviewed tree. |
| **Unreadable evidence is a failed call, not a short review** (R4). | Reader death or a traversal limit means "the failure signature may have been missed"; treating it as clean would turn a bound into a pass. |
| **`--ignore-user-config` is recorded but not adopted** (D1). | `mcp list` rejects it, so its effect on the MCP set cannot be observed model-free; adopting it would also change the sandbox backend setting. It is a human decision. |

## Work items

| Finding | Change | Proof |
|---|---|---|
| R1 | `state.thread_debt(marker, bound_thread)`; `cmd_reconcile` converts a derived debt to a pending marker before clearing process uncertainty and refuses on failure; `_settle_unbound_thread` uses the same derivation; the service records `unbound_thread` for a fresh announced thread at the in-doubt write | `R1_*` |
| R2 | `_check_session_health`: `running` + dead owner => `uncertain` in the primary record first, pending marker second (any phase); in-doubt write retried, `uncertainty_marker_unwritable`; whole-record writes (`marker.update`, `write_marker`) | `R2_*` |
| R3 | `execpath.trusted_which` checks the real target; `resolve_cli` validates shim-derived `node.exe`/`codex.js` (`cli_target_untrusted`); `system_tool` uses `GetSystemDirectoryW`; tools/tests use `gitsafe.trusted_git` | `R3_*` |
| R4 | `_gather_text_ex` iterative with reported limits; only request-shaped keys skipped; oversize lines scanned with JSON ASCII escapes resolved (window 2048, anchored type prefix); `reader_failed` / `evidence_truncated_items` => `turn_evidence_incomplete` before the infra check | `R4_*` |
| R5 | `parse_wrapper` evaluates in program order, rejects reserved/reused/shadowed names; `patch_matches` compares the complete argument | `R5_*`, `test_apply_patch_attack.py` |
| R6 | `names_write_denial` parses `Failed to <verb> <PATH>`, normalises and compares the full path under the recorded base; unparseable => INCONCLUSIVE | `R6_*` |
| R7 | `begin_maintenance` releases the lock on every post-lock failure and reports `maintenance_stale`; admin commands exit 1 when the flag could not be removed | `R7_*` |
| R8 | `abort_before_exec` corrects facts first, guards the clear, writes a `refused_before_exec` marker cleared by the next health check | `R8_*` |
| R9 | recorder runs tests from a staged copy; the prompt gate validates every embedded record and reconciles the summary with the retained log; `tools/prefix_check.py` retains the overlay procedure and raw output | `R9_*` |
| R10 | `run_turn` creates all bookkeeping before `Popen`; `_terminate_owned`/`_still_running` guard `poll()` | `R10_*` |
| R11 | `telemetry.sanitise` requires `isinstance(value, str)` before enum membership | `R11_*` |
| R12 | no prefix exemption; exact codes registered in `ctx.bannered`; unexpected servers seen post-audit => `capabilities.mcp_unexpected_seen` | `R12_*` |
| R13 | `tools/smoke_oracles.py` derives control/attempt/refusal from the recorded rollout; unchanged filesystem is evidence only with attempt AND refusal recorded; tool inventory is a labelled self-report | `R13_*` (pure functions only) |
| N10 (retained) | `registry.prev.json` proves loss even when `instance.json` is gone (`registry_lost`) | `N10_*` |
| V12 (retained) | `UNBORN` only when `show-ref --verify` exits 1; reasons `head_incomplete`/`head_failed`/`head_unreadable`/`head_unverifiable` | `V12_*` |
| Evidence wording | classes `implementer_executed` / `source_observation` / `historical_report` / `assumption`; model-free CLI commands are `model_free_cli_observation`; README section references corrected (live checks are §11) | `R9_*`, README |

## Found by the implementer while remediating (not in GPT's list)

* `adopt` first passed the bound thread as the "already bound" argument and so settled nothing.
* `quarantine` returned 0 even when it failed to remove the maintenance flag.
* One R7 test patched `Path.exists` for the second registry too and proved nothing.
* One R4 reader test died before `turn.completed`, so it did not discriminate.
* The escaped-signature test also contained other signatures, so it passed for the wrong reason.
* The raw `mcp list --json` output in evidence leaked local paths (now normalised to name/enabled).

## Not done / not authorised

Live model calls (including sending the round-4 prompt), registration/activation, D1/D3/D8.
The MCP configuration race (no deny-all in CLI 0.162.1) remains documented, not closed.
