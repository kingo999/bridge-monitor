<!-- GPT's verification review, verbatim. Advisory; NOT approval. Metadata: request_20261013T121903Z.json -->

Advisory verdict: **defer activation**. The sixth remediation repairs several specific defects, but smoke enforcement attribution remains unsound, the observer fence does not cover durable writes, and prefix validation still trusts outcome metadata it claims to reconstruct. No Critical finding is established.

No tools were used. Recorded executions, fingerprints and machine-state claims were not independently verified. **CLOSED** below means the specified source defect is repaired within the stated scope; it does not establish live sandbox enforcement.

## 1. Verdict tables

### T1–T12

| Finding | Verdict | Claim assessment and source basis | Tests and remaining work |
|---|---|---|---|
| **T1 — Abandoned observers** | **PARTIALLY CLOSED** | **CONTRADICTED as “only while the fence is open.”** `service.py::_locked_call::observe` fences `marker.update`, then performs `Registry.update_marker_if` outside the fence. The compare-and-merge does prevent the original stale whole-record replacement and recreation of a removed/different-call record. `runner.py::Events.feed/finish` synchronize parser mutations and exclude the observer from the Events lock. | The two abandoned-callback tests meaningfully detect the original overwrite/recreation. Neither tests a retained record with the **same call ID**, nor a persistence failure reported after the caller and telemetry have finished. Resolve U2; preserve observed identity on runner exceptions, U8. |
| **T2 — Shell semantics** | **NOT CLOSED** | **CONTRADICTED.** `smoke_oracles.py::writes_target` still treats positional payloads as target operands. `tokenize` accepts cmd expansion syntax such as `%NAME%`, despite claiming variables are rejected. `is_git_commit` accepts global options that change the operation or repository. `_refused` does not establish which operation/target caused a denial. | T2 tests catch the supplied quoted/compound forgeries, unknown tools and malformed arrays. They omit positional payloads, shell-specific expansion and commit-target changes. U1. |
| **T3 — Validated purity** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the enumerated conflicts; CONTRADICTED as general purity validation.** `state.py::conflicting_persistence_record` recognizes listed reasons and literal `process_uncertain is True`. `is_pure_thread_debt` otherwise infers purity from the state and absence of those recognized conflicts. | Three admin/service tests behaviorally cover the original conflicting-reason case. The two helper tests initially fail only because APIs are absent. Add positive marker invariants and conservative handling of other explicit process facts. |
| **T4 — Ordinary ordering** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the pending-mirror gate; CONTRADICTED for “final writes are checked and reported.”** `_locked_call` gates pending persistence on `uncertainty_durable`, but ignores the final retained-record `write_marker` result in the pointer-failure branch. Cleanup lock failures can also escape after a pointer is committed. | The ordinary uncertainty/mirror negative is useful. `test_unchecked_completion_and_observer_writes_are_reported` tests **completion**, not observer or final retained-record failure. U3. |
| **T5 — Encoded carry window** | **CLOSED** | **CONFIRMED FROM SOURCE within the declared signature and escape bounds.** `runner.py::_OVERSIZE_WINDOW`, `OversizeScan._scan` and `_unescape_json` retain enough raw bytes for the supported maximally encoded signature. | Two boundary tests discriminate the old behavior; the window-size assertion is structural. Add fully escaped signature letters as a compatibility guard. Runs exceeding the whitespace bound and arbitrary Unicode escapes remain declared exclusions. |
| **T6 — Generic-exception warnings** | **CLOSED** | **CONFIRMED FROM SOURCE.** `service.py::ArchitectService.execute` attaches warnings to the newly wrapped `BridgeError` and preserves the original exception as its cause. | The warning test behaviorally detects the old omission. The cause test correctly remains a regression guard. Late asynchronous warning mutation remains U2. |
| **T7 — Observed thread** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE** for `runner.py::turn_problems` and `apply_patch_attack_test.py::stage_b`: an observed valid ID is required, with equality on resume. **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL** for the current `send_verification.py::review_is_valid` implementation; its body is omitted. | Missing-ID and mismatch tests express the right requirements. The sender test exercises its public function, but the Stage-B portion is a source-string assertion. Supply the sender body and add a behavioral Stage-B harness test. |
| **T8 — Reconstructed evidence chain** | **PARTIALLY CLOSED** | **CONTRADICTED as full independent reconstruction.** `prefix_problems` reconstructs supported AST identities and required overlay hashes, but obtains outcomes from `RESULT_JSON`; it does not independently derive them from test result lines. Guard/pass declarations remain trusted. An empty AST identity set disables identity comparison. | Overlay and snapshot negatives are useful. The accepted fixture lacks a failure traceback and unittest footer, demonstrating the remaining weak gate. U5–U7. |
| **T9 — JavaScript whitespace** | **CLOSED** | **CONFIRMED FROM SOURCE for inter-token whitespace.** `apply_patch_attack_test.py::JS_WHITESPACE`, grammar regexes and statement stripping restrict it to the four declared ASCII characters. | The U+0085 negative discriminates the old behavior. Ordinary whitespace and lone quoted CR are appropriate guards. Some rejected characters are valid JavaScript whitespace; conservative rejection is the stated design. |
| **T10 — Establishment witnesses** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for `Registry._write`; CONTRADICTED as a registry-wide first-write guarantee.** `_write` requires the initial backup before committing and journals establishment. `Registry.reinit` bypasses that policy and relies on a best-effort event appended by `cmd_reinit`. Missing-identity recovery wording is repaired. | Backup-failure and `register_new` tests are useful. `test_reinit_is_part_of_the_witness_policy` checks event-set membership, not reinit’s actual witness durability. U4. |
| **T11 — Raw-byte omission** | **CLOSED** | **CONFIRMED FROM SOURCE.** `make_verification_prompt.py::assemble` compares raw bytes before omission, then normalizes/scrubs only displayed content. | The line-ending test detects the former normalized-text comparison. Actual round-6 omission modes still require the detached current manifest. |
| **T12 — Outcome label** | **CLOSED** | **CONFIRMED FROM SOURCE.** `record_evidence.py::cmd_tests` gives provenance mismatch precedence as `INVALID`; ordinary unsuccessful tests become `FAILED`. | The stage-mutation test discriminates the old label. The failing-test guard preserves the intended distinction. |

### S1–S15

| Finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **S1 — Preserve process facts during disposition** | **PARTIALLY CLOSED** | `admin.py::_settle_unbound_thread` retains raw/uncertain records and the enumerated conflicting persistence records. S1/T3 tests cover those paths. General purity remains inferred by exclusion; validate marker invariants before deletion. |
| **S2 — Durable settlement** | **PARTIALLY CLOSED** | `state.py::thread_debt` honors `thread_settled`; `update_marker_if` preserves unrelated settlement fields. S2 tests cover reset→reconcile and previously bound fresh records. Complete the lifecycle fence and atomic read-modify-write boundary, U2. |
| **S3 — Check primary persistence** | **PARTIALLY CLOSED** | `_check_session_health` checks conversion writes; `_locked_call` now gates ordinary pending mirroring. S3/T4 negatives discriminate those defects. The final retained write remains unchecked, U3. |
| **S4 — Require completed stdout reading** | **CLOSED** | `runner.py::run_turn` calls `Events.finish` after joins on normal return; missing EOF or a live reader makes evidence incomplete. S4 and T1 frozen-event tests cover the parser protocol. Late events are discarded only after evidence is declared incomplete. |
| **S5 — Attribute smoke operations** | **NOT CLOSED** | `smoke_oracles.py::writes_target/is_git_commit/_refused` still allow false target/operation attribution. S5/T2 tests repair selected forgeries but miss U1. |
| **S6 — Complete output scanning** | **PARTIALLY CLOSED** | `_gather_text_ex` handles named returned containers; `OversizeScan` now covers the declared encoded window. S6/T5 tests support those repairs. Unknown output-container names and unobserved live item shapes remain limits, not complete coverage. |
| **S7 — System tools fail closed** | **CANNOT VERIFY** | S7 tests would detect environment-derived fallback launches. Current `execpath.py::system_tool` and `codexcli.py::kill_tree/running_lock_holders` bodies are omitted; archived verdicts and matching hashes are documentary support only. |
| **S8 — JavaScript lexical validity** | **CLOSED** | `apply_patch_attack_test.py::parse_wrapper/_split_statements` reject reserved bindings and raw quoted CR/LF; T9 repairs inter-token whitespace. S8/T9 tests cover the specified defects. This remains a limited grammar, not a general JavaScript parser. |
| **S9 — Post-run staged fingerprint** | **CLOSED** | `record_evidence.py::cmd_tests/run_problems` rehash the stage before deletion. S9’s functional persistent-mutation test would invalidate a changed stage. Rewrite-and-restore and environment substitution remain disclosed limits. |
| **S10 — Checkable prefix chain** | **PARTIALLY CLOSED** | `prefix_problems` now requires the supported overlay and rehashes snapshot bytes. T8 negatives cover those additions. Actual outcome reconstruction, guard derivation, empty AST handling and executed-overlay isolation remain U5–U7. |
| **S11 — Guarded no-execution cleanup** | **CLOSED** | `service.py::_locked_call::abort_before_exec` corrects facts first, guards cleanup, and reports unsuccessful fallback. S11 tests meaningfully exercise spawn and late-audit refusal failures. Post-success cleanup is a separate U3 issue. |
| **S12 — Warnings on error exits** | **CLOSED** | `ArchitectService.execute/_attach_warnings` handle categorized and generic exceptions. S12/T6 tests cover both branches. The remaining late observer-warning race is U2. |
| **S13 — One-write establishment witness** | **PARTIALLY CLOSED** | `Registry._write` now requires the first backup and emits establishment. S13/T10 tests cover ordinary first writes. `Registry.reinit` lacks the same mandatory witness policy, U4. |
| **S14 — Reviewed sender validity** | **PARTIALLY CLOSED** | `runner.py::turn_problems` now requires observed identity. S14/T7 tests express sender requirements. Supply the omitted current `send_verification.py` body for direct verification. |
| **S15 — Exact banners and flag reporting** | **CLOSED** | `_locked_call` registers the exact persistence warning; `admin.py::_end_maintenance_reporting` reports retained flags on exceptional paths. S15 tests meaningfully cover both repairs. |

### R1–R13

| Finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **R1 — Thread debt on every path** | **PARTIALLY CLOSED** | `state.py::thread_debt`, admin settlement and S1/S2/T3 tests repair ordinary recovery. A runner exception can discard an ID already parsed but not yet delivered to the observer, U8; general marker purity also remains bounded. |
| **R2 — Durable process fact** | **PARTIALLY CLOSED** | `_check_session_health` and `_locked_call` check primary uncertainty before mirroring. R2/T4 tests cover stale and ordinary ordering. Check/report the final fallback and synchronize marker read-modify-write, U2/U3. |
| **R3 — Execution targets** | **CANNOT VERIFY** | R3 tests require real-target and derived-artifact rejection, with simulated links when necessary. Current `execpath.py` and `codexcli.py::resolve_cli` bodies are omitted. |
| **R4 — Evidence completeness** | **PARTIALLY CLOSED** | `Events.finish`, `_gather_text_ex` and `OversizeScan` repair the specified parser defects; R4/S4/S6/T5 tests are relevant. Live item shapes remain absent, and Stage A does not require complete capture, U9. |
| **R5 — Wrapper semantics** | **CLOSED** | `parse_wrapper/_resolve_arg/patch_matches` implement ordered binding resolution, exact complete arguments and the repaired whitespace subset. R5/S8/T9 tests cover those defects. Unknown outer-tool attribution remains a separate oracle issue in U1. |
| **R6 — Exact denial target** | **CLOSED** | `apply_patch_attack_test.py::names_write_denial/_canonical_path` compare full parsed write targets under a base directory. R6 and attack-oracle tests cover substring, other-directory and read-denial negatives. Live path spellings remain unverified. |
| **R7 — Maintenance** | **CLOSED** | `Registry.begin_maintenance/end_maintenance` and `_end_maintenance_reporting` preserve ownership/release and report exceptional retained flags. R7/S15 tests cover the specified failures. |
| **R8 — Refusal cleanup** | **CLOSED** | Shared `abort_before_exec` and R8/S11 tests repair facts-first refusal cleanup and failed fallback reporting. |
| **R9 — Evidence provenance/gate** | **PARTIALLY CLOSED** | `cmd_tests` has qualified stage/live endpoint binding; `assemble` uses raw-byte omission. R9/S9/T11 tests support those repairs. Prefix outcome and execution-boundary gaps remain U5–U7. |
| **R10 — Cleanup gaps** | **CLOSED** | `runner.py::run_turn/_terminate_owned/_still_running` place post-spawn bookkeeping in cleanup and guard failing polls. Embedded R10 tests cover poll masking and allocation ordering. Full older lifecycle tests are omitted. |
| **R11 — Typed telemetry** | **CLOSED** | `telemetry.py::sanitise` checks types before enum membership and preserves valid fields. `test_review4.py::R11_TypedTelemetryFields` would catch the specified unhashable-value failure. |
| **R12 — Exact warning visibility** | **CLOSED** | `_locked_call` uses exact rendered codes; `execute` now attaches generic-error warnings. R12/S15/T6 tests cover the identified suppression paths. |
| **R13 — Smoke oracles** | **NOT CLOSED** | Recorded outer calls are better attributed, but `writes_target/is_git_commit/_refused` remain unsound. R13/S5/T2 tests omit U1’s counterexamples. |

### V1–V15

| Finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **V1 — Runner cleanup** | **CLOSED** | `runner.py::run_turn` retains exception-safe ownership/thread/job cleanup. R10 and N3/N4 tests cover important paths. The full `V1_RunnerCleanup` test body is omitted; observed-ID loss is U8. |
| **V2 — Only durable record deleted** | **PARTIALLY CLOSED** | Service/admin retain blocking records and replace debt before removal. S1/T3/T4 tests cover supported states. Positive purity validation and complete fallback reporting remain required. |
| **V3 — Hard owner death** | **PARTIALLY CLOSED** | `_check_session_health` requires reconciliation for dead owners; S1 and N3/N4 tests reject ordinary reset/adopt bypass. General conflicting-record validation remains incomplete. |
| **V4 — Telemetry boundary** | **CLOSED** | `service.py::_telemetry_dir/_emit` validate configured and fallback destinations through `validate_state_dir`. N2 tests cover rejection without probing. Concurrent filesystem replacement remains outside the declared boundary. |
| **V5 — Forged wrappers** | **CLOSED** | `parse_wrapper`, ordered `_resolve_arg` and `patch_matches` repair the specified wrapper semantics. Attack negatives plus S8/T9 cover the intended subset. Restricting the outer call name remains U1. |
| **V6 — Executable selection** | **CANNOT VERIFY** | The relevant current resolver/system-tool bodies are omitted. R3/S7 tests express the requirements; hashes cannot reconstruct the implementation. |
| **V7 — Infrastructure completeness** | **PARTIALLY CLOSED** | Runner scanning now covers named returned containers, EOF failure and the supported encoded window. R4/S6/T5 tests discriminate those repairs. Unknown shapes and Stage-A incomplete capture remain. |
| **V8 — Recovery commands** | **PARTIALLY CLOSED** | `cmd_reset/cmd_adopt/cmd_reconcile`, `thread_debt` and S1/S2/T3 tests preserve the supported independent facts. General purity and exception-time observed identity remain incomplete. |
| **V9 — Maintenance** | **CLOSED** | State/admin maintenance ownership, stale-flag refusal and release are source-visible. N5/R7/S15 tests cover cooperating-owner and cleanup failures. |
| **V10 — Capability recheck** | **PARTIALLY CLOSED** | `_locked_call` performs final and post-turn MCP checks and exposes warnings. N8/R12/S12 tests cover drift behavior. Audit implementation is omitted; the final-load race and runtime effectiveness remain unverified. |
| **V11 — OS-denial attribution** | **CLOSED** | `names_write_denial` requires a parsed write-type denial of the complete target. R6/attack tests cover unrelated/read denials. This does not repair smoke’s broader refusal oracle. |
| **V12 — Fingerprint completeness** | **CLOSED within declared Git scope** | `fingerprint.py::_read_head/_run_capture/repo_fingerprint` reject incomplete reads, verify unborn refs and hash staged/worktree differences separately. V12 and N11/N15 tests cover those behaviors. Sampling is not atomic. |
| **V13 — Evidence binding** | **PARTIALLY CLOSED** | `cmd_tests` binds an executed stage and detects persistent stage/live differences. S9/T12 tests cover mutation and labels. Prefix reconstruction/isolation remains incomplete. |
| **V14 — Warning visibility** | **CLOSED** | Success, categorized-error and generic-error rendering are source-visible in `_locked_call/execute/_attach_warnings`; S12/S15/T6 cover them. Asynchronous warnings after finalization remain U2. |
| **V15 — Exact formats** | **CLOSED for supplied validators** | `config.py` validators, `ids.py::is_thread_id`, state thread checks and `telemetry.py::sanitise` use full matching/type checks. N14/R11 tests are relevant. CLI-origin capability-name validation requires the omitted CLI body. |

### N1–N15, P3 and F1–F8

| Finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **N1 — Host Git containment** | **CLOSED within declared boundary** | `gitsafe.py::safe_git_argv/safe_git_env` apply the documented controls; N1’s full hostile-helper test requires textconv, clean and fsmonitor controls to fire, and includes a removal mutation check. Executable resolver verification is separately missing; `info/attributes` remains excluded. |
| **N2 — State validation** | **CLOSED** | Admin entry points, service state/telemetry selection and `state.py::validate_state_dir` validate before access. N2 tests check that rejected destinations are not created or probed. |
| **N3 — Ownership** | **PARTIALLY CLOSED** | `jobobject.py::ProcessOwnership` and `runner.py::run_turn` implement suspended Windows assignment/resume and job accounting. N3/N4 tests cover sequencing and refusal. Service-spawned children remain unobservable; full current lifecycle outcomes are not live evidence. |
| **N4 — Exceptions/stale recovery** | **PARTIALLY CLOSED** | `process_identity/owner_is_alive` conservatively handle unreadable identity and PID reuse; `_check_session_health` refuses stale owners. N3/N4 tests cover those cases. Complete observed-ID recovery and marker invariants remain. |
| **N5 — Maintenance** | **CLOSED** | State/admin exclusive maintenance lifecycle is source-visible; N5/R7/S15 tests cover ownership, quiescence and exceptional release/reporting. |
| **N6 — Durable admission/persistence** | **PARTIALLY CLOSED** | `_locked_call` requires durable admission and now gates dependent pending writes. N6/S3/T4 tests cover those points. Final fallback results and post-commit cleanup outcomes remain U3. |
| **N7 — Infrastructure evidence** | **PARTIALLY CLOSED** | Known failure items, signature-bearing errors and incomplete readers are rejected. N7/R4/S6/T5 tests cover fixtures. Live failed-command shapes remain missing. |
| **N8 — Capability drift** | **PARTIALLY CLOSED** | `_locked_call` rechecks before/after execution and attaches exposure warnings, including generic errors. N8/S12/T6 cover orchestration. Current CLI audit source and trustworthy runtime evidence remain missing; the race persists. |
| **N9 — Wrapper oracle** | **CLOSED for the specified nested-wrapper defects** | `parse_wrapper/_resolve_arg/patch_matches` plus attack negatives and T9 cover exact arguments, ordered bindings and supported lexical validity. The accepted **outer tool name** needs restriction, U1. |
| **N10 — Instance identity** | **PARTIALLY CLOSED** | `Registry._read/_write/restore_prev` fail closed on identity loss and require a first ordinary-write backup. N10/S13/T10 tests cover these paths. Reinit’s witness policy is incomplete, U4. |
| **N11 — Fingerprint completeness** | **CLOSED within declared Git scope** | `repo_fingerprint` separately hashes staged/worktree content and rejects incomplete status/diffs. Full N11/N15 tests discriminate staged-only and unborn changes. Declared Git visibility exclusions remain. |
| **N12 — Oracle attribution** | **PARTIALLY CLOSED** | Stage-A full-target denial comparison and attack negatives are sound for complete supplied output. Smoke attribution remains U1, and Stage A omits capture-completeness checks, U9. |
| **N13 — Filename probe** | **CANNOT VERIFY** | Full N13/N15 tests require unsafe-name exclusion and direct `findstr` data arguments. Current `codexcli.py::_probe_file/sandbox_preflight` bodies are omitted. |
| **N14 — Identifier ingress** | **CLOSED** | `Events._feed_locked` validates through `ids.py::is_thread_id` before observation/storage; telemetry revalidates IDs. N14 tests would catch malformed-ID leakage through the specified path. |
| **N15 — Visible missing evidence** | **CLOSED within original scope** | `_locked_call` banners skipped preflight/probes and unavailable fingerprints; `execute` now preserves existing banners on generic exceptions. N11/N15, N13/N15 and T6 tests cover visibility. |
| **P3 — Degraded mode removal** | **CLOSED** | `config.py::load_settings` rejects every legacy non-`error` setting; `_locked_call` withholds detected infrastructure/incomplete-evidence answers. The production behavior is source-confirmed. |
| **F1 — Actual-turn failures** | **PARTIALLY CLOSED** | Runner/service reject supported infrastructure failures and incomplete streams. N7/R4/S4/S6/T5 tests cover fixtures. Current live event shapes remain absent. |
| **F2 — Capability containment** | **PARTIALLY CLOSED** | Visible service orchestration and records support a qualified configuration snapshot. Audit/resolver source is omitted; runtime removal, web mapping and final-load race remain unresolved. |
| **F3 — Process lifetime** | **PARTIALLY CLOSED** | Job ownership and conservative reconciliation are implemented. N3/N4 tests support important paths. Service children, late lifecycle writes and exception-time thread accounting remain limitations. |
| **F4 — Registry/admin lifecycle** | **PARTIALLY CLOSED** | Maintenance and ordinary establishment/recovery are stronger. General purity, reinit witnesses and uncaught administrative marker-lock failures remain. |
| **F5 — Partial/uncertain outcomes** | **PARTIALLY CLOSED** | Ordinary ordering and generic-error warnings are repaired. Final fallback writes, post-commit cleanup and late observer-warning finalization remain U2/U3. |
| **F6 — Review consistency** | **PARTIALLY CLOSED** | The bounded fingerprint implementation and meaningful staged/unborn tests are supplied. Its multiple Git/stat observations are not an atomic repository snapshot; retain the documented scope and pause edits. |
| **F7 — Attack-test validity** | **NOT CLOSED** | Smoke attribution still permits false evidence, U1. Stage A accepts incomplete capture, U9; outer attack-wrapper attribution and timeout labels also need correction. |
| **F8 — Evidence/documentation** | **PARTIALLY CLOSED** | Classes, endpoint limits, labels and raw-byte omission are improved. Prefix claims exceed reconstruction, several absolute lifecycle statements remain false, and important current source bodies are omitted. |

### Cross-cutting claims

| Claim | Assessment |
|---|---|
| **A — Durable process fact before dependent mirrors; disposition never removes it** | **CONTRADICTED as an unconditional guarantee.** The stale and ordinary mirror gates are source-correct for supported records. General purity is not established by `is_pure_thread_debt`; final retained-write reporting is incomplete. |
| **B — Independent durable facts; observers write only while their fence is open** | **CONTRADICTED as written.** `observe` performs durable I/O after leaving `_CallFence.run`. Compare-and-merge repairs the original destructive stale-record overwrite, but does not enforce the asserted lifecycle boundary. |
| **C — System tools and derived executable targets fail closed** | **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL.** Current `execpath.py` and `codexcli.py` bodies are omitted. Supplied tests and the archived review support the claim documentarily. |
| **D — EOF, frozen evidence and complete returned-container scanning** | **CONTRADICTED as universal completeness.** The runner’s parser mutation protocol now seals returned evidence and the encoded window is repaired. Returned-container recognition remains finite, live shapes are missing, and Stage-A capture completeness is not required. |

The historical claims that no registration, activation, live call or v1 modification occurred are **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL** as universal assertions. `integrity.json` supports narrower recorded comparisons and explicitly reports configuration-stat drift.

## 2. New findings

### U1 — High: Smoke attribution still permits a target mention to become enforcement evidence

**Location:** `tools/smoke_oracles.py::writes_target/tokenize/is_git_commit/_refused`; additionally `apply_patch_attack_test.py::wrapper_analysis`.

**Concrete counterexample:**

```powershell
Set-Content other.txt WRITE_ATTEMPT_FRESH.txt
```

The target name is the positional **value** written to `other.txt`. `writes_target` nevertheless treats it as an unflagged target operand. If the operation returns a denial for `other.txt`, `_refused` accepts it. With an unchanged filesystem, the create-file step can receive PASS although the requested target was never attempted.

Other source-visible attribution gaps:

- `tokenize` accepts `%NAME%` and `!NAME!`; cmd expansion is not opaque data and can change command structure.
- Shell-independent quote removal does not preserve cmd’s different quote semantics.
- `is_git_commit` accepts `git -C other_repo commit …`, without binding the operation to the disposable repository.
- `is_git_commit` also skips global action-changing options rather than requiring the requested commit command.
- `wrapper_analysis` parses non-direct bodies without requiring the recorded outer name to be `exec`. An unknown tool carrying suitable JavaScript text can therefore be marked authentic.

**Why existing tests miss it:** T2 exercises flagged `-Value`, quoted command mentions and compound commands. It does not exercise positional parameter roles, alternate commit roots or unknown outer attack-tool names.

**Minimal fix:** Recognize exact probe operations with tool/shell-specific schemas. Track positional path versus value arguments. Restrict commit evidence to the requested repository and exact supported command. Reject expansion/escaping syntax outside that shell’s supported subset. Require outer attack calls to be direct `apply_patch` or the explicitly supported `exec` wrapper.

### U2 — Medium: The fence closes memory mutation, not durable observer work or warning mutation

**Location:** `service.py::_locked_call::observe`, `_CallFence`; `state.py::Registry.update_marker`.

An observer can complete `fence.run(marker.update)`, pause, and call `update_marker_if` after `fence.close`. If recovery retains the same call’s uncertain record, the compare condition still matches. The write remains allowed after the claimed lifecycle boundary.

If that late operation fails, it appends `inflight_observer_write_failed` to `ctx.warnings` after the returned error/answer and telemetry may already have been finalized. That failure is then not reliably caller-visible.

Also, `Registry.update_marker` reads outside `markers.lock` and later performs a whole-record write. Serialization of individual writes is not serialization of the read-modify-write transaction; a concurrent merge between those operations can be lost.

**Qualification:** The supplied merge-only fields do not establish the original settlement-erasure exploit against normally generated records. The original whole-record observer replacement is repaired.

**Why tests miss it:** T1 substitutes a different call ID or removes the record. Both make the compare fail regardless of whether the fence is closed. No test retains the same call and observes late failure reporting.

**Minimal fix:** Give durable observer rights a lifecycle/generation condition enforced with the marker transaction, and finalize warning state only after observer work is accounted for. Make `update_marker` read/merge/write under one marker lock. If the weaker guarantee is intentional, narrow the documentation accordingly.

### U3 — Medium: Marker-lock failures after commitment produce incomplete or misleading outcomes

**Location:** `service.py::_locked_call` persistence branch; `admin.py::cmd_reset/cmd_adopt/cmd_reconcile/main`.

Two distinct cases remain:

1. Binding fails, pending persistence fails, and the **final retained-record write** fails. Its Boolean result is ignored, contradicting T4’s checked-final-write claim.
2. Binding succeeds, but `clear_marker` times out acquiring the new global lock. `OSError` escapes the persistence block, which catches only `BridgeError`. The caller receives an internal error although the pointer was committed and the answer was otherwise usable.

Administrative commands similarly perform pointer or pending mutations before an uncaught `OSError` from marker clearing. `admin.main` catches only `BridgeError`, so the command can exit through a traceback without a structured partial-result explanation.

**Why tests miss it:** T4 fails completion writes, not the final fallback. S11 covers proven-no-execution cleanup, not post-bind cleanup. Admin tests do not hold `markers.lock` across disposition commands.

**Minimal fix:** Check every fallback result. Treat post-commit cleanup as a distinct reported partial/blocked outcome. Convert administrative marker-lock failures into actionable categorized errors that describe completed mutations.

### U4 — Medium: `reinit` bypasses the mandatory witness policy

**Location:** `state.py::Registry.reinit`; `admin.py::cmd_reinit`.

`Registry.reinit` writes an established identity and registry directly, without creating `registry.prev.json`. Its journal witness is appended afterward by the admin command and is best-effort.

If that append fails, then registry and identity are subsequently lost, no independent witness necessarily remains. The later state can again resemble first initialization.

**Why tests miss it:** `T10.test_reinit_is_part_of_the_witness_policy` checks that event names appear in `_ESTABLISHED_EVENTS`; it never executes reinit with failed journaling and subsequent identity/registry loss.

**Minimal fix:** Apply the mandatory pre-commit witness policy to reinit as well. Test the actual reinit lifecycle with failed journal persistence.

### U5 — Medium: Prefix outcomes and declared guards are still trusted

**Location:** `make_verification_prompt.py::prefix_problems`; `prefix_logic.py::classify_errors`; `tests/helpers.py::write_prefix_evidence`.

The builder compares recorded outcomes with the log’s `RESULT_JSON`, but does not independently reconstruct outcomes from per-test result lines and failure blocks. Two matching implementer-generated metadata objects are not independent outcome reconstruction.

For any outcome marked `fail`, `classify_errors` assigns `assertion` without requiring an assertion traceback. The accepted fixture contains a FAIL header and `RESULT_JSON`, but no traceback or unittest completion footer.

The builder also trusts `undeclared_passes`, `loader_errors` and `skipped` rather than deriving all of them from outcomes/log evidence.

**Why tests miss it:** T8’s “complete chain” fixture embodies these omissions. Negatives alter selected fields, without supplying internally matching metadata that contradicts the actual result lines.

**Minimal fix:** Parse unique, paired per-test outcomes; reconcile failure/error blocks and footer totals; derive skipped/loader/pass sets; compare passing tests with explicit declared guards.

### U6 — Medium: An empty AST result disables identity validation

**Location:** `prefix_problems`; `prefix_logic.py::expected_test_ids`.

The identity comparisons run only when `expected` is truthy. A valid module using an unsupported alias, such as an aliased `TestCase` import, can produce an empty set. The builder then skips both recorded-identity checks instead of refusing the unsupported analysis.

The AST helper also overstates what it reconstructs: imported inheritance, `load_tests`, dynamically attached tests, assignments overriding inherited methods and base aliases are not modeled.

**Why tests miss it:** The AST test covers direct `unittest.TestCase` and in-module inheritance only.

**Minimal fix:** Fail closed on an empty or unsupported analysis when outcomes are nonempty. Declare and validate the supported test-module subset. Add alias, override and `load_tests` negatives.

### U7 — Medium: Prefix source hashes do not fully bind the executed overlay

**Location:** `tools/prefix_check.py::main`.

The prefix overlay uses unrestricted `copytree` for snapshot `bridge/` and `tools/`, but records/checks only selected Python and fixture files. Extra copied artifacts, including importable bytecode caches if present, are not represented by that list. `-B` prevents bytecode writes; it does not prohibit reading existing caches.

Unlike the full-suite recorder, the prefix stage is neither made read-only nor fingerprinted after execution. A persistent overlay rewrite during the prefix run is therefore not detected.

**Why tests miss it:** T8 alters snapshot bytes during builder validation, not executed overlay bytes or extra copied artifacts.

**Minimal fix:** Construct the overlay from an explicit verified file list, exclude caches/unlisted artifacts, and retain before/after executed-overlay fingerprints. Disclose this boundary separately from full-suite staging.

### U8 — Medium: A runner exception can lose an already observed thread ID

**Location:** `runner.py::run_turn` exception path; `Events.feed`; `service.py::_locked_call` runner-error branch.

The reader can parse a valid `thread.started` and set `Events.thread_id`, then pause before `on_thread` enters the service fence. A supervision exception causes bounded cleanup and a raised `BridgeError`. The service closes the fence and derives thread debt only from its local `marker`; it does not receive the frozen Events snapshot.

The late observer is now correctly refused, but the thread identity already observed by the parser can remain absent from the uncertain record. Reconciliation can then clear that record without a thread-disposition debt.

**Why tests miss it:** T1 blocks persistence after the service’s memory update. It does not block between parser observation and callback entry while supervision fails.

**Minimal fix:** Preserve the final validated observed identity/evidence on runner exceptions and use it during service recovery. Test this scheduling point explicitly.

### U9 — Medium: Stage A does not reject incomplete command capture

**Location:** `apply_patch_attack_test.py::stage_a`, `judge_os_attack`.

Stage A uses `Simple` outputs and return codes without checking `Simple.complete`. A truncated or failed capture containing an early matching denial can be accepted as enforcement evidence if the filesystem is unchanged.

The documentation’s fail-closed capture contract applies to configuration readers, but should also apply to an oracle relying on captured denial text.

**Why tests miss it:** Stage-A oracle tests supply complete strings. None injects an incomplete `Simple` result into `stage_a`.

**Minimal fix:** Make incomplete capture INCONCLUSIVE before evaluating controls or attacks. Preserve the capture-failure reason.

### U10 — Low: Harness outcome wording conflates failure and inconclusive evidence

**Location:** `apply_patch_attack_test.py::stage_b`; `live_smoke_test.py::main`.

- Stage B reports every `turn_problems` result as FAIL, including timeout/cancellation, although its module contract says timeout/launch failure is INCONCLUSIVE.
- The smoke harness always adds an INCONCLUSIVE runtime-inventory check, so its overall result remains inconclusive even when every enforcement step is conclusive.
- Its final explanation says INCONCLUSIVE means write blocking was not exercised, which is false when only inventory remains unverified.

**Why tests miss it:** They inspect predicate use and oracle verdicts, not complete harness reporting.

**Minimal fix:** Separate enforcement conclusions from inventory evidence and distinguish observed violations from unavailable evidence.

**Lock review:** No session/registry/marker lock cycle is established from the supplied call graph. Marker methods release their lock before callers proceed to registry operations. However, the new lock is global across sessions, its order is absent from the documented lock hierarchy, and the five-second timeout bounds **acquisition**, not `fsync` or other I/O while holding it. “Never block forever” is therefore broader than the implementation establishes.

## 3. Test adequacy

The embedded prefix record and raw log agree on **41 outcomes: 29 assertion failures, five API-missing errors and seven passes**. These are implementer-recorded results.

The classification is accurate as an exception/result classification. The wording “29 behavioural assertion failures” is too strong: several assertions check constants, wording or source structure.

| Area | Recorded pre-fix outcomes | What the tests establish |
|---|---|---|
| **T1** | Two assertions; one API error | The two threaded tests behaviorally detect stale whole-record replacement/recreation. The freeze test stops at missing `Events.finish` on pre-fix code. Same-call retained-state and pre-callback exception races are absent. |
| **T2** | Four assertions; one guard | Meaningful negatives for quoted/compound programs, generic refusal wording, unknown tool names and malformed arrays. They do not validate all operand roles or shell semantics. |
| **T3** | Three assertions; two API errors | Admin/service tests behaviorally discriminate the original conflicting-reason handling. Helper tests fail at missing APIs before testing purity behavior. |
| **T4** | Two assertions; one guard | One detects ordinary mirror ordering; one detects completion-write reporting. Despite its name, the latter does not test observer or final fallback reporting. |
| **T5** | Three assertions | Two behaviorally detect encoded signatures split across chunks. One checks window size structurally. |
| **T6** | One assertion; one guard | Warning propagation is behavioral; cause preservation is an appropriate compatibility guard. |
| **T7** | Two assertions; one API error | Missing identity and sender propagation discriminate behavior. Resume equality stops at the missing keyword API. Stage-B use is checked textually, not by invoking a mocked Stage B. |
| **T8** | Five assertions; one API error; one guard | Overlay completeness, snapshot rehashing, duplicate passing identities and failure-kind comparison are useful gate negatives. AST inheritance stops at the missing module. The accepted-chain guard has incomplete underlying result evidence. |
| **T9** | One assertion; two guards | U+0085 between tokens behaviorally discriminates the old regex. Ordinary whitespace and lone quoted CR preserve existing behavior. |
| **T10** | Four assertions | First-backup failure and new-session witnesses exercise behavior. Recovery text checks wording rather than the workflow. Reinit checks event membership rather than actual witness persistence. |
| **T11** | One assertion | Meaningfully checks raw-byte omission policy through `assemble`. |
| **T12** | One assertion; one guard | Persistent stage mutation detects the changed label; ordinary failing tests correctly remain `FAILED`. |

The five API-only failures are exactly:

1. T1 frozen-snapshot test: missing `Events.finish`.
2. T3 conflicting-record helper test: missing purity API.
3. T3 consistent-record helper test: missing purity API.
4. T7 resume test: missing `expected_thread` parameter.
5. T8 inheritance test: missing `prefix_logic`.

The seven declared passes are correctly identified:

1. Ordinary failing tests remain `FAILED`.
2. A supported single simple command remains attributed.
3. Durable uncertainty still permits pending mirroring.
4. Original exception cause remains preserved.
5. The supplied prefix fixture remains accepted.
6. Lone raw CR in a quoted literal remains rejected.
7. Ordinary ASCII JavaScript whitespace remains accepted.

**Tests passing for incomplete or wrong reasons:**

- `T8.test_a_complete_chain_is_accepted` passes with no failure traceback or unittest footer. It endorses insufficient evidence, not a complete reconstructed result chain.
- The first half of `test_identities_and_kinds_are_reconstructed_not_trusted` can fail because `RESULT_JSON` disagrees, without proving AST identity reconstruction.
- `T10.test_reinit_is_part_of_the_witness_policy` passes while `Registry.reinit` bypasses the mandatory backup path.
- `T4.test_unchecked_completion_and_observer_writes_are_reported` does not exercise the observer behavior named in its title.
- T2’s supported-command guard establishes fixture compatibility, not sound denial attribution for every accepted shape.

Older mutation sensitivity was not re-established. The omitted older test modules also prevent direct assessment of their full assertions in this call.

## 4. Evidence

The evidence classes and historical separation are substantially honest:

- Source fingerprints distinguish file observation from executed environment metadata.
- CLI observations are implementer-executed, model-free records.
- Historical smoke/attack results are excluded as current-source enforcement evidence.
- Integrity evidence retains exit 1 and the configuration-stat difference.
- Source and all-files snapshot hash scopes are distinguished.
- The staged-file attribute and endpoint-detection limits are explicitly disclosed.

**The full-suite staging chain is sufficient for qualified implementer-executed provenance**, assuming the recorder and its records are trusted. It binds the recorded result to an executed staged source set and detects persistent endpoint differences. It does not independently authenticate execution, freeze the environment or detect rewrite-and-restore.

The supplied full-suite record lists equal live-before, live-after, staged-before and staged-after hashes. The passing raw log is not embedded, so its actual identities, pairing and interleaving cannot be independently assessed here. Reviewing `log_problems` establishes what the gate attempts to check; it does not establish that the gate ran successfully on that particular log.

The prefix chain is weaker than the full-suite chain because of U5–U7.

| Capability level | Assessment |
|---|---|
| **resolved** | Appropriate for the CLI’s recorded effective-config snapshot. It does not establish runtime tool removal or that execution loaded the same snapshot. |
| **requested** | Appropriate for sandbox/approval argv requests and the unverified web configuration key. |
| **uncontained** | Appropriate for capabilities with no applied control and allow-listed servers. An observed enabled server is a configuration exposure, not proof it was loaded or invoked. |

Wording still needing correction:

- The MCP probe proves that **the empty-table override did not disable the observed servers**. It does not prove that no supported isolation mechanism exists.
- The historical rollout proves that **that rollout** had no separately named nested apply_patch records. It does not establish a universal persistence rule.
- `features list` retention is visibly a clipped tail, not a complete readout.
- “Only while the fence is open,” “every final write checked,” “mandatory first-write witness” across reinit, and “complete reconstructed outcomes” exceed the supplied implementation.
- The five-second marker-lock timeout does not bound I/O performed while holding the lock.
- `live_smoke_test.py`’s opening description says the repository is created in system temp, while its default implementation uses `.smoke_work`.

The declared limits should additionally include prefix-stage mutation/import artifacts, shell-specific attribution assumptions, and the missing outcome reconstruction described above.

## 5. Decisions and recommended live-check gates

These are recommendations, **not authorization**.

| Decision | Options | Recommendation |
|---|---|---|
| **D1 — Configuration/capability isolation** | Retain per-server overrides; investigate isolated configuration/`--ignore-user-config`; permit reviewed exceptions | Retain the empty default allow-list. Investigate `--ignore-user-config` separately with explicit backend selection and fresh/resumed evidence. Its rejection by `mcp list` does not establish its runtime MCP effect. |
| **D3 — Activation** | Defer; later side-by-side evaluation; cut-over | **Defer activation.** Resolve the High oracle defect and lifecycle/evidence gaps before relying on evaluation conclusions. If the human later selects evaluation, prefer a distinct side-by-side server name. |
| **D8-web** | Keep requested-disabled; allow research under an explicit policy | Keep requested-disabled. Runtime absence remains unverified; exceptions require a human-selected egress policy. |
| **D8-delegation/image** | Keep gates disabled; selectively enable | Keep disabled pending trustworthy runtime evidence. Model self-report is insufficient. |
| **P2 — Service children** | Accept uncertainty; retain operator reconciliation; develop stronger observation | Retain conservative reconciliation after interruption/bridge death. Complete observer and exception-time thread accounting. |
| **P3 — Degraded reviews** | Retain removal; diagnostic-only reporting; restore degraded success | Retain removal. Infrastructure-failed or incomplete evidence should continue to withhold the review. |

Recommended gates for `REVIEW_PACKAGE.md` §11:

| Proposed check | Findings that should gate reliance on its conclusions |
|---|---|
| **Fingerprint/evidence preparation** | U5–U7; supply the omitted source needed for direct review. T11/T12’s specific defects are repaired. |
| **Disposable status/preflight** | Supply current resolver and preflight bodies. This can collect readiness diagnostics; it does not establish write enforcement. |
| **Live smoke enforcement conclusions** | **U1**, plus shell/tool-specific operation schemas and correct U10 reporting. |
| **Stage A enforcement conclusions** | U9 complete-capture handling and current launcher/preflight source. |
| **Stage B enforcement conclusions** | Restrict outer wrapper names, correct U10 timeout classification, and behaviorally test shared validity handling. |
| **Service/stdio recovery conclusions** | U2–U4 and U8; marker-lock failure scenarios must have explicit outcomes. |
| **Verification sender validity** | Supply `send_verification.py`; verify its complete predicate and answer handling directly. |

A separately human-authorized diagnostic run could collect missing real shapes while remaining explicitly inconclusive. This review approves no live run, registration or activation.

## 6. Cheapest next checks

In priority order:

1. **Pure oracle negatives:** Positional `Set-Content other.txt <probe-name>`, alternate-root commits, global Git action options, cmd expansion syntax, shell-specific quoting and unknown outer wrapper names. Settles U1.
2. **Threaded lifecycle test:** Pause after the fence’s memory mutation; retain a same-call uncertain record; close the call; release the observer and inject persistence failure. Assert no unaccounted late writes/warnings. Settles U2.
3. **Marker-lock failure matrix:** Hold the real marker lock while clearing after successful bind and during reset/adopt/reconcile. Assert pointer/history facts and actionable partial outcomes. Include failed final retained writes. Settles U3.
4. **Observed-ID exception race:** Pause between `Events.thread_id` assignment and callback entry, then induce supervision failure. Require durable thread debt or explicit identity recovery. Settles U8.
5. **Reinit witness test:** Fail journal append, execute reinit, then remove registry/identity. Require loss detection through a mandatory independent witness. Settles U4.
6. **Prefix gate forgeries:** Matching record/`RESULT_JSON` with contradictory result lines; missing traceback/footer; skipped/loader outcomes hidden by empty declarations; unsupported AST aliases and empty reconstruction. Settles U5/U6.
7. **Prefix execution isolation:** Extra cache/artifact and persistent overlay-mutation cases; require invalid provenance. Settles U7.
8. **Mock Stage A and Stage B:** Incomplete `Simple` capture, timeout/cancel, missing answer/identity and filesystem change. Assert correct conclusive/inconclusive labels. Settles U9/U10.
9. **Model-free platform diagnostics, if human-selected:** Real launcher/link resolution, suspended assignment, clean `findstr` readiness and per-hunk denial path spellings.
10. **Freshly human-authorized live capture:** Obtain actual failed-command JSON, shell-wrapper shapes and trustworthy capability availability; then run repaired fresh/resumed smoke/attack checks and scripted stdio recovery against disposable state. Bind results to source and configuration.

## 7. Missing material

For direct verification beyond the supplied bodies:

- Current **`bridge/codexcli.py`**, **`bridge/execpath.py`** and **`tools/send_verification.py`**. They are hash-listed/archived but omitted here; they are necessary for claims C, S7, R3, V6, N13 and sender/audit verification.
- Current **`bridge/redact.py`** if the diagnostic-redaction claim is to be directly reviewed.
- Full current **`tests/test_review3.py`**, **`tests/test_remediation.py`**, **`tests/test_service.py`**, and the unchanged-only test modules where their older assertions are relied upon. Their hashes and archived descriptions do not supply test bodies.
- **`docs/evidence/tests_sixth_remediation_full.log`** for direct per-test passing-log assessment.
- The detached **round-6 verification manifest** alongside the prompt. It need not be embedded recursively; its exact file modes and prompt binding are not established by the previous manifest.
- Complete effective-feature readouts and the referenced web configuration schema for direct assessment of those observations.
- A defined inventory of prefix-stage copied/importable artifacts and executed-overlay before/after evidence.
- Current-source runtime evidence for shell-call shapes, failed-command events, denial paths, capability availability and service-child outcomes.
- A scripted stdio/service live check with explicit success, refusal, completeness, interruption and recovery oracles.
- An equivalent policy/control-plane record if universal “no registration/live call/v1 change” claims must be established; the supplied integrity record supports only its recorded comparison scope.

The core config, fingerprint, Git containment, ID, job, telemetry, server, entry-point and requested test bodies are now supplied. Their previous missing-material issues are resolved; the remaining omissions above are not supplied by the 421-test total.
