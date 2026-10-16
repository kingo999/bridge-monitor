# Remediation checklist 6 (sixth round: GPT verification findings T1-T12)

Trigger: GPT's verification review of the fifth remediation,
`docs/gpt_verification/gpt_verification_20261013T094415Z.md` (no Critical finding; defer activation;
S5 and R13 NOT CLOSED, ten S findings PARTIALLY CLOSED, cross-cutting claims A, B, D CONTRADICTED; new
findings T1-T12, 2 High). Authorised by the human: "yes remediate T1-T12". Same boundaries: isolated v2
only, no live model calls, no registration, no activation, no sending (a separate authorisation is
required for the round-6 send).
Diff base: `.baseline_post_remediation5/` (exactly the state GPT verified).

Process note: the list was written while implementing. Each new test in `tests/test_review6.py` was run
against the frozen snapshot with `tools/prefix_check.py` (REVIEW_PACKAGE §5 has the numbers and the
failure kinds). The round-5 review also showed that omitting unchanged files made five older findings
unverifiable: every file it named as missing is embedded in full again.

## Decisions that shaped several findings

| Decision | Why |
|---|---|
| **A late writer must be unable to write, not merely unlikely to** (T1). Two independent layers: an in-process FENCE (closed before any recovery write) and a durable compare-and-merge (`Registry.update_marker_if`: only into a record that still carries this call's `call_id`; never recreates). Observers merge their own fields; they never persist a whole record. | A Boolean checked at method entry cannot stop an already-entered callback. |
| **Events is a frozen snapshot after the join** (T1). All mutators take one lock and re-check the seal; the thread observer is called AFTER the lock is released, so sealing never waits for it. | `seal()` previously did not exclude a `feed` already running. |
| **Parse the command, then decide** (T2). One simple command or nothing; a verb is a word in command position. | Substring matching accepted quoted text and unreachable branches. |
| **Validate purity** (T3). `persistence_failed` + a process-uncertainty reason is a process fact. | One field decided whether a record could be deleted. |
| **Durable-first on every path** (T4) and a **mandatory first-write witness** (T10). | The stale-owner branch was repaired, the ordinary path was not; the first backup was best-effort. |
| **Reconstruct, don't re-read** (T8). Identities from the syntax tree, kinds from the raw log, overlay from bytes on disk. | The builder trusted the record it was supposed to check. |

## Work items

| Finding | Change | Proof |
|---|---|---|
| T1 | `service._CallFence`, `observe()` (merge-only); `state.Registry.update_marker_if`, `markers.lock` around every marker write/clear; `runner.Events` lock + `finish()`; observers called outside the lock | `T1_*` |
| T2 | `smoke_oracles.tokenize/writes_target/is_git_commit/is_rev_parse_head`, known tool names, reserved result names, mandatory commas, specific `REFUSAL_RE` | `T2_*` |
| T3 | `state.conflicting_persistence_record/is_pure_thread_debt`; `admin._settle_unbound_thread`, `cmd_reconcile`; `_check_session_health` converts a conflicting record | `T3_*` |
| T4 | `uncertainty_durable` gates the pending mirror; completion/final/observer writes checked and reported | `T4_*` |
| T5 | `_OVERSIZE_WINDOW` 8192; README wording narrowed to ASCII `\u00XX` + simple escapes | `T5_*` |
| T6 | the generic-exception branch attaches warnings before raising | `T6_*` |
| T7 | `runner.turn_problems(result, expected_thread)`; stage B uses it | `T7_*` |
| T8 | `tools/prefix_logic.py`; `prefix_problems` (module in overlay, complete overlay, rehash, identities, kinds); `log_problems` (unique, paired) | `T8_*` |
| T9 | `JS_WHITESPACE` (space, tab, CR, LF) in every grammar regex and strip | `T9_*` |
| T10 | first-write backup mandatory and before the commit; `registry_established` journal event; honest recovery advice | `T10_*` |
| T11 | `assemble` compares RAW bytes | `T11_*` |
| T12 | `INVALID` for broken provenance, `FAILED` for failing tests | `T12_*` |
| missing material | `ALWAYS_FULL` lists every file the round-5 review named; the three old test modules are embedded in full | builder |

## Not done / not authorised

Live model calls (including sending the round-6 prompt), registration/activation, D1/D3/D8. The MCP
configuration race (no deny-all in CLI 0.162.1) remains documented, not closed. Rewrite-and-restore of a
staged file during a run is not detected (the post-run fingerprint sees persistent differences only).
The npm launcher's own JavaScript execution is not examined. Returned-container key names outside the
finite `_OUTPUT_KEYS` list are not treated as output containers.
