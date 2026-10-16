# Remediation checklist 3 (third round: GPT verification findings V1-V15)

Trigger: GPT's verification review of the second remediation,
`docs/gpt_verification/gpt_verification_20261012T171145Z.md` (no Critical finding; N4/N6/N9 NOT
CLOSED, most others PARTIALLY CLOSED; new findings V1-V15, 7 High). Authorised by the human:
"remediate V1-V15". Same boundaries: isolated v2 only, no live calls, no registration, no sending.
Diff base: `.baseline_post_remediation2/` (exactly the state GPT verified; combined hash in its
`MANIFEST.json`).

Honest note on process: unlike round 2, this list was written while implementing (the design is
recorded as it was decided), not before. Each row names the proof; every new test was also run
against the frozen snapshot to confirm it fails there (see REVIEW_PACKAGE).

## Decisions that shaped several findings

| Decision | Why |
|---|---|
| **No stale in-flight record is ever auto-recovered** (V2, V3). A dead owner leaves the call `uncertain`; only `reconcile` clears it. | Job closure only proves the job's members are gone; sandbox-service commands are outside the job, and nothing durable proves the turn did not complete. GPT's P2 recommendation: extend the conservative default to hard-owner death. |
| **Process uncertainty and thread disposition are two facts with two resolutions** (V2, V8). `reconcile` confirms processes; `adopt`/`reset` decide a thread. | The first remediation conflated them in one marker state. |
| **Executable selection is a separate boundary from git flags** (V6). One resolver (`bridge/execpath.py`) never searches the current directory or the reviewed tree. | `CreateProcess`/`shutil.which` look in the working directory first, and the MCP server's working directory IS the reviewed project. The same hole existed for `codex`, `node`, `taskkill`, `tasklist`, not only `git`. |
| **The wrapper oracle accepts a strict pass-through grammar, nothing else** (V5). | Textual heuristics are defeated by unreachable calls and runtime-assembled strings; what the call *receives* is decoded from its argument. |

## Work items

| Finding | Change | Proof |
|---|---|---|
| V1 | `runner.run_turn`: everything after `Popen` (incl. `ProcessOwnership()`/`Events()`) inside the cleanup scope; threads tracked only once `start()` succeeded (readers first, prompt writer last); `owner.close()` in an outermost `finally`; job-wide terminate on every non-clean exit even when the root already exited | `V1_RunnerCleanup`: start failure, constructor failures, descendant of an exited root |
| V2 | `_check_session_health`: pending marker must be durable before the in-flight record is removed; a running-phase dead-owner marker becomes `uncertain` (+ pending marker for a fresh observed thread), never recovered | `V2V3_*` incl. failed pending write blocked twice |
| V3 | same: hard owner death requires `reconcile` | `test_hard_owner_death_requires_operator_reconciliation...` |
| V4 | `_telemetry_dir` validates every candidate incl. the fallback; none safe => skip telemetry with a content-free stderr line | `V4_*` (home == repository) |
| V5 | `parse_wrapper` strict grammar; `evaluate_turn` compares what the call received; label stays `wrapper_attested` | forged-wrapper negatives incl. GPT's `if (false)` form |
| V6 | `bridge/execpath.py`; `gitsafe.trusted_git`; `resolve_cli`, `kill_tree`, `running_lock_holders` use it; env scrub drops every `GIT_*` case-insensitively and sets `NoDefaultCurrentDirectoryInExePath` | `V6_*` (planted git/codex in repo and cwd) |
| V7 | `_gather_text` traverses every output container (skips only input-ish keys); oversized lines are streamed through the whole signature scan, prose item types excluded by parsing the type from the head | `V7_*` |
| V8 | `reconcile` refuses an unsaved-thread record; `adopt`/`reset` settle only the thread debt and keep independent process uncertainty; the service no longer overwrites `uncertain` with `persistence_failed` | `V8_*` |
| V9 | `begin_maintenance` refuses an existing (stale) flag (`maintenance_stale`); `end_maintenance` releases the lock on every path; `_require_quiescent` cleans up when the probe fails | `V9_*` |
| V10 | `recheck_mcp` returns the full snapshot (`unexpected`, `allowed_enabled`); allow-listed servers first enabled later are reported; the last read is after the durable marker and its refusal clears the marker | `V10_*` |
| V11 | write-operation denial tied to the attacked path (stage A and stage B); controls A1/A2 verify the update and delete effects too | oracle tests |
| V12 | `_run_capture` records reader exceptions; HEAD is `UNBORN` only when verified (symbolic ref whose target is missing), otherwise unavailable | `V12_*` |
| V13 | recorder fingerprints before AND after the run, rejects a change / nonzero exit / skips, retains the raw log (hash recorded); the prompt gate checks all of it; `integrity` and `cli` evidence subcommands retain v1/lock output and CLI help/schema observations | `V13_*` |
| V14 | `ctx.warn(code, banner)`; a warning without caller-visible text is rendered generically; details are placed BEFORE the answer; README corrected | `V14_*` |
| V15 | `fullmatch` for every identifier/name validator; typed telemetry fields checked against their own enum/format | `V15_*` |

## Not done / not authorised

Live model calls (including re-sending a verification prompt), registration/activation,
D1/D3/D8. The MCP configuration race (no deny-all in CLI 0.162.1) remains documented, not closed.
