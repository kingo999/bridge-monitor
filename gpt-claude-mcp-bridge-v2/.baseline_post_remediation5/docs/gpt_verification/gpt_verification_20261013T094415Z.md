<!-- GPT's verification review, verbatim. Advisory; NOT approval. Metadata: request_20261013T094415Z.json -->

Advisory: **defer activation**. The original raw reset/adopt bypass is repaired for normally generated records, and stale-owner conversion now checks persistence before mirroring debt. However, two High findings remain: abandoned reader callbacks can overwrite later recovery state, and smoke oracles still permit false execution/refusal attribution.

No Critical finding is established. No tools were used, and recorded executions and hashes were not independently verified.

**1. Verdict tables**

“CLOSED” concerns the specified source defect within its stated scope; it does not establish live enforcement. Tests below describe what their assertions would detect. For omitted source bodies, the previous review remains documentary evidence, but I cannot independently re-verify their implementation from hashes.

| Finding | Verdict | Claim assessment and source | Test coverage and remaining work |
|---|---|---|---|
| **S1 — Preserve process facts during disposition** | **PARTIALLY CLOSED** | **CONTRADICTED as an unconditional guarantee.** `admin.py::_settle_unbound_thread` retains raw `running` and `uncertain` records. However, it assumes every `state="persistence_failed"` record is pure thread debt without checking conflicting process-uncertainty facts. | `S1_DispositionCommandsNeverSettleProcessFacts` meaningfully tests raw-record retention, subsequent refusal and reconciliation. Add conflicting-record validation; see T3. No normal current writer is shown to generate that conflicting combination. |
| **S2 — Durable settlement** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for ordinary, serialized transitions.** `state.py::thread_debt` honors `thread_settled`; reset supplies `bound=None`, so a previously bound fresh uncertain thread receives settlement. `_check_session_health` preserves it through `update_marker`. The unconditional claim fails under a late observer write. | S2 tests exercise reset→reconcile and `pointer_bound=True`, fixing the previous omission. They do not exercise an abandoned observer overwriting settlement; T1. |
| **S3 — Check primary persistence** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for stale-owner conversion; CONTRADICTED across all service paths.** `_check_session_health` checks three attempts and raises before mirroring. `_locked_call` still permits pending persistence after all ordinary uncertainty writes fail, and ignores observer/completion-marker results. | S3 tests detect the original stale-conversion defect. Add the ordinary-turn write-failure matrix and observer persistence failures; T4. |
| **S4 — Require completed stdout reading** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for withholding incomplete evidence.** `runner.py::run_turn` flags a live reader or missing EOF. **CONTRADICTED for event immutability:** `Events.seal` does not stop an already-entered `feed` or direct reader assignments. | The stalled-reader test catches the original false success. Its stall occurs before the real `feed` checks `_sealed`; it misses T1. Freeze returned evidence and fence late state observers. |
| **S5 — Attribute smoke operations** | **NOT CLOSED** | **CONTRADICTED.** `smoke_oracles.py::_native_operation/_js_operation` establish an outer call, but `_writes` still infers an executed write from substrings inside quoted text or unreachable shell branches. `_refused` does not attribute refusal to that write. | S5 rejects several original forgeries, but accepts broader counterexamples; T2. Require a narrow executable-command grammar and attributable denial evidence. |
| **S6 — Complete output scanning** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the named returned containers and short JSON escapes; CONTRADICTED as complete scanning.** `_gather_text_ex` repairs returned `input/arguments`. `OversizeScan` retains only 4096 raw bytes, insufficient for the supported 1024 whitespace characters encoded with six-byte escapes. | S6 covers short escapes and a short split escape, not a maximally encoded signature crossing chunks; T5. Unrecognized returned-container names remain an explicitly acknowledged limitation. |
| **S7 — System tools fail closed** | **CLOSED** | **CONFIRMED FROM SOURCE.** `execpath.py::system_tool` raises `SystemDirectoryUnavailable`; `codexcli.py::kill_tree/running_lock_holders` catch failure without constructing an environment-derived executable. | S7 tests exercise failed OS lookup and inspect actual attempted launches. Root-only fallback is correctly bounded. |
| **S8 — JavaScript lexical validity** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for reserved bindings, raw quoted CR/LF rejection and template normalization.** The broader strict-grammar claim remains **CONTRADICTED** because Python `\s` accepts characters that JavaScript does not treat as whitespace. | Reserved-word negatives are useful. The CR test does not isolate lone raw CR. Add whitespace lexical negatives; T9. |
| **S9 — Post-run staged fingerprint** | **CLOSED** | **CONFIRMED FROM SOURCE.** `record_evidence.py::cmd_tests` applies read-only attributes and fingerprints the stage again before deletion; `run_problems` rejects mismatch or missing staged-after evidence. | The functional mutation test detects a persistent staged rewrite. It does not prove continuous immutability or detect rewrite-and-restore. The outcome-label mismatch is T12. |
| **S10 — Checkable prefix chain** | **PARTIALLY CLOSED** | **CONTRADICTED as an end-to-end validation guarantee.** `prefix_check.py::main` verifies copied files at execution time. `make_verification_prompt.py::prefix_problems` subsequently trusts several record assertions, permits incomplete overlay lists, and does not validate failure classifications against tracebacks. | S10 tests reject the supplied inconsistent fields, but the “consistent” fixture itself lacks the claimed test module. Strengthen independent reconstruction; T8. |
| **S11 — Guarded no-execution cleanup** | **CLOSED** | **CONFIRMED FROM SOURCE for the specified refusal paths.** `_locked_call::abort_before_exec` corrects facts first, guards cleanup, retries and reports failed fallback persistence, and is reused for spawn/ownership refusal. `settle_failed_turn` guards deletion failure. | S11 behaviorally tests spawn cleanup failure and failed late-refusal fallback. Add coverage of failed-turn deletion/fallback for completeness, but the specified repair is visible. |
| **S12 — Warnings on error exits** | **PARTIALLY CLOSED** | **CONTRADICTED as “every error exit.”** `ArchitectService.execute` attaches warnings to caught `BridgeError`; its separate generic-exception branch creates and raises a new `BridgeError` without attachment. | S12 tests cover categorized errors only. Add generic exceptions after warnings are recorded; T6. |
| **S13 — One-write establishment witness** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for successful backup/journal writes; CONTRADICTED as guaranteed first-write evidence.** `Registry._write` attempts the first backup but silently tolerates failure. Journal establishment evidence is also best-effort; `register_new` has no establishment journal event. | S13 tests assume witness writes succeed. Add failed-backup and failed/unreadable-journal cases, and correct recovery instructions when identity is absent; T10. |
| **S14 — Reviewed sender validity** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE that the sender is included and shares `turn_problems`; CONTRADICTED for stable thread identity.** `runner.py::turn_problems` does not require a thread ID. | S14’s successful fixture has no thread ID and explicitly passes. Require a valid observed ID; T7. |
| **S15 — Exact banners and flag reporting** | **CLOSED** | **CONFIRMED FROM SOURCE.** `_locked_call` registers the exact `persistence_code`; `admin.py::_end_maintenance_reporting` reports unsuccessful clearing within exceptional cleanup paths. | S15 exercises the previously suppressed warning and both exceptional maintenance paths. |

The cross-cutting claims remain broader than the implementation:

| Claim | Assessment |
|---|---|
| **A — Process fact durable before mirrored debt** | **CONTRADICTED.** The stale-owner branch is repaired, but `_locked_call` can write pending debt after all uncertainty writes fail. `_settle_unbound_thread` also identifies purity solely by state. See T3–T4. |
| **B — Independent, durable process/disposition facts** | **CONTRADICTED as an unconditional guarantee.** The normal derivation and settlement logic are repaired, but an abandoned observer’s whole-record write can erase settlement or overwrite a later call’s record. See T1. |
| **C — Trusted system tools and selected targets** | **CONFIRMED FROM SOURCE within the declared boundary.** `execpath.py::trusted_which/trusted_target/system_tool` and `codexcli.py::resolve_cli` validate the searched targets and derived Node/script artifacts. The npm launcher’s subsequent behavior remains outside that boundary. |
| **D — EOF and complete returned-output scanning** | **CONTRADICTED as a completeness guarantee.** EOF failure now withholds answers, but sealing is not an immutable snapshot, returned-container recognition remains bounded, and oversized scanning misses a supported escaped signature across chunks. |

| R finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **R1 — Thread debt on every path** | **PARTIALLY CLOSED** | `state.py::thread_debt`, admin disposition/reconciliation and S1/S2 repair ordinary raw recovery and debt resurrection. Fence late observer writes and validate conflicting records; T1/T3. |
| **R2 — Durable process fact** | **PARTIALLY CLOSED** | `_check_session_health` checks persistence; `_locked_call` still lacks the same condition before pending writes. T4. |
| **R3 — Execution targets** | **CLOSED** | `execpath.py` and `codexcli.py::resolve_cli` repair the specific target and OS-directory defects. Complete npm behavior remains a disclosed trust limitation, not verified containment. |
| **R4 — Evidence completeness** | **PARTIALLY CLOSED** | `run_turn`, `_gather_text_ex`, `OversizeScan` and S4/S6 repair several defects. T1/T5 remain; live failed-command shapes are unavailable. |
| **R5 — Wrapper semantics** | **PARTIALLY CLOSED** | `parse_wrapper/_resolve_arg/patch_matches` and S8 repair binding semantics and named lexical defects. T9 remains. |
| **R6 — Exact denial target** | **CLOSED** | `apply_patch_attack_test.py::names_write_denial/_canonical_path` compare a parsed full target under the base directory. Live engine spellings and aliases remain unverified. Full unchanged test bodies are omitted. |
| **R7 — Maintenance** | **CLOSED** | `Registry.begin_maintenance/end_maintenance` and `_end_maintenance_reporting` protect ownership/release and report exceptional retained flags. S15 tests detect the reporting regression. |
| **R8 — Refusal cleanup** | **CLOSED** | Shared `abort_before_exec` and S11 tests address the identified facts-first and unsuccessful-fallback paths. |
| **R9 — Evidence provenance/gate** | **PARTIALLY CLOSED** | Functional staging is improved. `prefix_problems/log_problems` still validate incomplete metadata chains and counts rather than full identities; T8/T11. |
| **R10 — Cleanup gaps** | **CLOSED** | `runner.py::run_turn/_terminate_owned/_still_running` visibly retain the allocation and guarded-poll repairs. `test_review3.py::V1_RunnerCleanup` supplies substantive lifecycle tests. |
| **R11 — Typed telemetry** | **CANNOT VERIFY** | The archived review says CLOSED and hashes identify unchanged `telemetry.py`, but its `sanitise` body and the relevant full R11 test are omitted. Supply them for direct re-verification. |
| **R12 — Exact warning visibility** | **PARTIALLY CLOSED** | Exact persistence-code registration is repaired; generic error wrapping still drops accumulated warnings. T6. |
| **R13 — Smoke oracles** | **NOT CLOSED** | `smoke_oracles.py::_writes/_refused/_js_operation` remain unsound for broader recorded-call forgeries; T2. |

| V finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **V1 — Runner cleanup** | **CLOSED** | `runner.py::run_turn` and `V1_RunnerCleanup` address the specified supervision/setup defects. Late observers are a separate T1 defect. |
| **V2 — Only durable record deleted** | **PARTIALLY CLOSED** | Canonical debt replacement and raw-record retention are repaired. T1/T3/T4 prevent an unconditional durable-record guarantee. |
| **V3 — Hard owner death** | **PARTIALLY CLOSED** | `_check_session_health` refuses dead-owner records; S1 tests verify reset/adopt no longer bypass ordinary reconciliation. Conflicting-record handling remains T3. |
| **V4 — Telemetry boundary** | **CLOSED** | `service.py::_telemetry_dir/_emit`, `state.py::validate_state_dir` and `V4_TelemetryNeverEscapesTheStateBoundary` support the path-scoped repair. Concurrent link replacement remains outside the claim. |
| **V5 — Forged wrappers** | **PARTIALLY CLOSED** | Exact patch decoding/comparison and binding restrictions are implemented. T9 remains; smoke attribution has separate T2 defects. |
| **V6 — Executable selection** | **CLOSED** | Current resolver and system-tool source repair the identified selection defects. Real link behavior and the remaining launcher trust boundary require platform evidence. |
| **V7 — Infrastructure completeness** | **PARTIALLY CLOSED** | Contextual returned-key handling and reader completion improve coverage; T1/T5 and unobserved live shapes remain. |
| **V8 — Recovery commands** | **PARTIALLY CLOSED** | Ordinary process/thread separation is repaired and tested by V8/S1/S2. Late record writes and conflicting states remain T1/T3. |
| **V9 — Maintenance** | **CLOSED** | Maintenance ownership/release and exceptional reporting are source-visible and covered by V9/S15. |
| **V10 — Capability recheck** | **PARTIALLY CLOSED** | `recheck_mcp`, discovery/final/later allowed-server union and guarded refusal cleanup are implemented. Generic error visibility and final-load races remain. |
| **V11 — OS-denial attribution** | **CLOSED** | `names_write_denial` requires a write-type denial of the complete parsed target. Actual engine formatting remains unverified. |
| **V12 — Fingerprint completeness** | **CANNOT VERIFY** | Archived CLOSED, unchanged hash, and full V12 tests support retention, but current `fingerprint.py::_read_head/_run_capture` bodies are omitted. |
| **V13 — Evidence binding** | **PARTIALLY CLOSED** | `cmd_tests` now detects persistent staged changes; full prefix-chain validation remains incomplete, T8/T11. |
| **V14 — Warning visibility** | **PARTIALLY CLOSED** | Success and categorized-error rendering improve; generic errors still bypass attachment, T6. |
| **V15 — Exact formats** | **CANNOT VERIFY** | Full V15 tests express appropriate requirements, and several admin/state call sites use `fullmatch`. Omitted `ids.py`, config and telemetry validators prevent full source re-verification. |

| N finding / P3 | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **N1 — Host Git containment** | **PARTIALLY CLOSED** | The `test_review2.py` diff confirms `REQUIRED = KINDS`, including fsmonitor. Current `gitsafe.py` and the complete hostile-helper/mutation test bodies are omitted; supply them to verify actual containment and control adequacy. |
| **N2 — State validation** | **CLOSED** | `admin.py::_context/_state_only/cmd_status`, service telemetry destination selection and `state.py::validate_state_dir` enforce the visible entry-point boundary. |
| **N3 — Ownership** | **PARTIALLY CLOSED** | Suspended attach/resume and cleanup orchestration are visible in `run_turn`; actual `jobobject.py` implementation is omitted. Service children remain an operator fact, and T1 affects durable lifecycle accounting. |
| **N4 — Exceptions/stale recovery** | **PARTIALLY CLOSED** | Conservative owner identity and ordinary stale-record retention are repaired. T1/T3 remain; obtain the omitted ownership implementation for complete lifecycle review. |
| **N5 — Maintenance** | **CLOSED** | Exclusive lock/flag lifecycle and exceptional retained-flag reporting are implemented in state/admin. |
| **N6 — Durable admission/persistence** | **PARTIALLY CLOSED** | Admission is checked, and ordinary disposition recovery is stronger. Late observers and unchecked post-admission persistence remain T1/T4. |
| **N7 — Infrastructure evidence** | **PARTIALLY CLOSED** | Known failed items and incomplete readers are rejected. T5 and supported live event-shape capture remain. |
| **N8 — Capability drift** | **PARTIALLY CLOSED** | Before/after MCP checks and accumulated named observations are implemented. Generic error visibility and the final configuration-load race remain. |
| **N9 — Wrapper oracle** | **PARTIALLY CLOSED** | Original patch/order/shadowing repairs are retained; T9 remains. |
| **N10 — Instance identity** | **PARTIALLY CLOSED** | First-backup attempts and journal witnesses improve one-call loss detection. Witness failures and impossible identity-loss recovery advice remain T10. |
| **N11 — Fingerprint completeness** | **CANNOT VERIFY** | Current fingerprint implementation is omitted. Full F6/V12 tests and the archived review provide documentary support, not direct source verification. |
| **N12 — Oracle attribution** | **PARTIALLY CLOSED** | Stage-A full-target denial parsing is source-visible. Smoke attribution remains unsound, T2; actual denial formats are missing. |
| **N13 — Filename probe** | **CLOSED** | `codexcli.py::_probe_file/sandbox_preflight` reject unsafe names and pass the selected filename directly to `findstr`. Real readiness remains unverified. |
| **N14 — Identifier ingress** | **CANNOT VERIFY** | `Events.feed` calls the central validator before observation/storage, but `ids.py::is_thread_id` is omitted. Full V15 tests would catch malformed ingress if executed as recorded. |
| **N15 — Visible missing evidence** | **CLOSED within its original success-path scope** | `_locked_call` renders skipped probes and unavailable consistency on successful answers. Generic error-warning propagation remains S12/T6. |
| **P3 — Degraded mode removal** | **PARTIALLY CLOSED** | `_locked_call` unconditionally withholds detected infrastructure/incomplete-evidence answers. `F1_ActualTurnFailures.test_degraded_mode_was_removed_and_cannot_be_enabled` would test legacy configuration rejection, but `config.py::load_settings` is omitted. |

| F finding | Verdict | Source/test basis and specific remaining work |
|---|---|---|
| **F1 — Actual-turn failures** | **PARTIALLY CLOSED** | Known failures and incomplete readers are withheld. T5 and current-source live failed-command shapes remain. |
| **F2 — Capability containment** | **PARTIALLY CLOSED** | Current audit/readout completeness and snapshot reporting are stronger. Runtime removal, web behavior, final-load race and T6 remain. |
| **F3 — Process lifetime** | **PARTIALLY CLOSED** | Ordinary raw recovery bypass is repaired. T1 affects lifecycle records; job implementation is omitted, and service children remain unobservable. |
| **F4 — Registry/admin lifecycle** | **PARTIALLY CLOSED** | Maintenance/reporting and typical one-write loss detection improve. T3/T10 remain. |
| **F5 — Partial/uncertain outcomes** | **PARTIALLY CLOSED** | Normal settlement is durable and independently reconciled. T1/T4/T6 prevent the blanket contract from holding. |
| **F6 — Review consistency** | **PARTIALLY CLOSED** | Full tests and documentation express bounded Git consistency; the actual fingerprint/Git-containment bodies are omitted. Sampling is not atomic. |
| **F7 — Attack-test validity** | **NOT CLOSED** | Smoke false attribution remains T2; wrapper lexical validity and Stage-B validity checks remain T9 and the limitation discussed below. |
| **F8 — Evidence/documentation** | **PARTIALLY CLOSED** | Evidence classes and staged records improve. Prefix-chain checks, sender identity checks, omission rules and several absolute claims remain overstated. |

Additional claims:

- **CONFIRMED FROM SOURCE:** `Simple.complete` is checked by version, auth, catalog, feature and MCP readers; `capability_audit` now unions allowed servers over discovery and final readouts; the displayed N1 control requires all three helper kinds; the displayed R4 tautology is replaced with a meaningful assertion.
- **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL:** every actual Git launch in all tools/tests is trusted. Several bodies are omitted, and `Also_TestSuiteHygiene` scans only selected spellings.
- **CONFIRMED FROM SOURCE:** `log_problems` counts headers and `ok` lines. **CONTRADICTED as complete individual accounting:** it does not require unique test identities or pair each result with its header.
- **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL:** universal historical claims that nothing was registered, no other live call occurred, or v1 was never changed. The supplied records support narrower recorded observations.

**2. New findings**

**T1 — High: An abandoned state observer can overwrite settlement or a later call**

- **Location:** `runner.py::Events.feed/seal/run_turn`; `service.py::_locked_call::on_thread/on_started`.
- **Scenario:** The stdout reader enters `feed`, passes `_sealed`, and blocks inside `on_thread` persistence. The root exits; the bounded join expires; the service returns an error and releases the session lock. Recovery then settles or reconciles the record. When the observer resumes, its whole-record write uses the old in-memory `marker`, which contains no administrative `thread_settled` field. It can erase settlement, recreate a removed record, or overwrite a newly admitted call’s marker.
- **Qualification:** The incomplete answer remains withheld. The established defect is late mutation and unsafe state accounting, not a demonstrated successful review from that incomplete stream.
- **Why tests miss it:** S4 blocks before the real `feed` enters. After release, its `_sealed` check prevents processing. No test blocks an already-entered observer and then performs recovery or a subsequent admission.
- **Minimal fix:** Fence state writes by call generation and synchronize retirement with observer writes. An obsolete callback must be unable to update a record after lifecycle ownership is relinquished. Return a frozen event snapshot; a Boolean checked only at method entry is insufficient.

**T2 — High: Smoke oracles still confuse shell text with an executed write**

- **Location:** `smoke_oracles.py::_writes/_refused/_js_operation/probe_steps`.
- **Scenario:** An attributed native shell call executes:

  ```powershell
  Write-Output "Set-Content WRITE_ATTEMPT_FRESH.txt"; Get-Content unreadable.txt
  ```

  `_writes` finds the quoted `Set-Content` and target. If the actual read returns “Permission denied,” `_refused` accepts it. An unchanged filesystem can then receive a PASS for a write that was never attempted.

  Similarly, an unreachable `Add-Content canary.txt TAMPERED` branch followed by a denied read satisfies the append matcher.

- **Other gaps:** `_js_operation` does not restrict shell operations to `_SHELL_FUNCTIONS`; it does not reject reserved result identifiers. `_literal_arguments` accepts adjacent array literals without mandatory commas.
- **Why tests miss it:** S5 tests reject pure printing wrappers and straightforward read commands, but omit quoted command mentions, unreachable shell writes, unrelated denials, unknown tool names and malformed arrays.
- **Minimal fix:** Accept a small grammar of exact, single probe commands with known tool names and arguments. Reject arbitrary shell programs. Require refusal evidence attributable to the particular write, including relevant target/operation and outcome information.

**T3 — Medium: “Pure persistence debt” is inferred from one unvalidated field**

- **Location:** `admin.py::_settle_unbound_thread/cmd_reconcile`; `state.py::read_marker`.
- **Scenario:** A conflicting record contains `state="persistence_failed"`, a valid debt thread, and `reason="service_children_unobservable"` or another explicit uncertainty reason. Reset/adopt deletes it because purity is determined solely by `state`. Reconcile also treats that state as necessarily thread-only.
- **Qualification:** No normal current writer is shown to create this conflicting combination. This is a malformed/inherited-record contract gap, not a demonstrated normal-path bypass.
- **Why tests miss it:** The pure-debt guard constructs a consistent record and declares its purity from the same state.
- **Minimal fix:** Validate marker invariants and fail closed on conflicts. Prefer an explicit process-disposition fact; permit thread-only deletion only when process settlement is established.

**T4 — Medium: Ordinary-turn persistence still violates durable-first ordering**

- **Location:** `service.py::_locked_call`, uncertainty, completion and pointer-failure branches.
- **Scenario:** Admission and early thread observation persist. All three uncertainty writes fail, the completion-phase write fails, binding raises, but writing pending succeeds. The service has mirrored thread debt without successfully persisting the categorized process uncertainty.
- **Qualification:** The retained raw record still blocks ordinary reuse under the repaired S1 logic. This counterexample contradicts claim A; it does not by itself establish automatic unsafe reuse.
- **Why tests miss it:** S3 injects failures in stale-owner `update_marker`, not this ordinary-turn sequence. Observer and final retained-marker writes are also unchecked.
- **Minimal fix:** Track successful primary persistence explicitly. Require it before dependent mirroring, check observer/final writes, and expose the precise durable state when persistence remains unsuccessful.

**T5 — Medium: The oversized carry window misses a supported escaped signature**

- **Location:** `runner.py::OversizeScan._scan`, `_OVERSIZE_WINDOW`, `_unescape_json`.
- **Scenario:** A line contains `CreateProcess`, followed by 1024 `\u0020` escapes, followed by `{`. That is within the declared whitespace bound. Place a chunk boundary after, for example, 900 escapes. The first scan lacks `{`; the retained 4096 raw bytes omit `CreateProcess`; the next scan cannot reconstruct the signature.
- **Why tests miss it:** S6 tests short escapes and a short split escape. It never spans a boundary with the full encoded whitespace run.
- **Minimal fix:** Retain enough raw bytes for the maximum encoded signature, with escape-boundary handling, or incrementally decode and scan decoded carry. Also narrow “every JSON escape” wording: the current decoder handles ASCII `\u00XX`, not all Unicode escapes.

**T6 — Medium: Generic exceptions discard recorded warning banners**

- **Location:** `service.py::ArchitectService.execute`.
- **Scenario:** Disabled preflight or an allowed-server exposure records a warning. A later operation raises `OSError`, such as reading the answer file or clearing a marker after binding. The generic branch wraps the exception but never invokes `_attach_warnings`.
- **Why tests miss it:** S12 supplies failures already represented as `BridgeError`.
- **Minimal fix:** Build the wrapped error, attach accumulated warnings, then raise it. Preserve the inference/history facts.

**T7 — Medium: A review without any observed thread ID is marked valid**

- **Location:** `runner.py::turn_problems`; `send_verification.py::review_is_valid`.
- **Scenario:** Exit 0, EOF, `turn.completed` and an answer exist, but no `thread.started` was observed. Both predicates report validity.
- **Why tests miss it:** S4 and S14 successful fixtures contain no thread ID and explicitly expect success.
- **Minimal fix:** Require a well-formed observed thread ID. Where applicable, also require it to match the requested resumed thread.

**T8 — Medium: Prefix validation trusts metadata it claims to re-verify**

- **Location:** `make_verification_prompt.py::prefix_problems/log_problems`; `prefix_check.py::main`.
- **Scenario:** A record retains matching hashes for a reduced overlay list but omits the test module and other executed files. The builder accepts any nonempty matching list. `expected_tests`, loader-error declarations, undeclared-pass declarations and failure classifications are not reconstructed from their underlying evidence.
- **Further gap:** Snapshot overlay entries are compared with manifest entries, not rehashed from snapshot bytes during builder validation. Passing-log counts can be satisfied by repeated headers/results for the same test.
- **Why tests miss it:** `write_prefix_evidence` intentionally produces an accepted record without the claimed test module. Negatives mutate individual fields but do not test completeness or independent derivation.
- **Minimal fix:** Require the complete expected overlay, hash its actual files, reconstruct expected test identities/counts using a non-executing inspection method, and reconcile outcomes and classifications with the raw test output. Validate unique passing-test identities.

**T9 — Medium: The wrapper grammar accepts non-JavaScript whitespace**

- **Location:** `apply_patch_attack_test.py` statement regexes and `parse_wrapper`.
- **Scenario:** `const\u0085r = await tools.apply_patch("<expected patch>"); text(r);` matches Python `\s`, but U+0085 is not JavaScript whitespace. The parser can attest a wrapper that cannot execute.
- **Qualification:** False authenticity does not establish that the real runtime would return policy-rejection text. Its normal runtime result should be a syntax failure.
- **Why tests miss it:** S8 covers selected reserved words and CR/LF inside literals, not whitespace between tokens.
- **Minimal fix:** Use an explicitly supported JavaScript whitespace set throughout the grammar. Prefer a deliberately small ASCII subset.

**T10 — Medium: First-write establishment evidence is still best-effort, and recovery advice can be impossible**

- **Location:** `state.py::Registry._write/_read/register_new/restore_prev/reinit`.
- **Scenario:** The first registry write succeeds but its backup fails silently. If the independent journal witness is unavailable, deleting registry and identity again permits initialization. A successful `register_new` has no journal establishment event at all.
- **Recovery problem:** `_read` recommends `restore-prev` when identity is missing, but `restore_prev` requires an existing valid identity with a matching ID. That command cannot resolve the described missing-identity state unaided.
- **Why tests miss it:** S13 assumes backup and journal success and tests refusal, not the advertised recovery workflow.
- **Minimal fix:** Require an independent establishment witness before claiming the guarantee, or explicitly report and document witness degradation. Explain that identity must be restored before `restore-prev`, otherwise use explicit reinitialization/adoption. Include `reinit` in the witness policy.

**T11 — Low: “Byte-identical” omission uses normalized-text equality**

- **Location:** `make_verification_prompt.py::assemble`.
- **Scenario:** Two files have different raw bytes but equal LF-normalized, privacy-scrubbed text. The current file is omitted as `unchanged_since_verified_snapshot`, although its raw hash can differ from the snapshot.
- **Why tests miss it:** The omission test supplies truly identical files.
- **Minimal fix:** Compare raw bytes for omission. Normalize/scrub only the displayed content and diffs.

**T12 — Low: Staged mutation receives the wrong recorded outcome label**

- **Location:** `record_evidence.py::cmd_tests`.
- **Scenario:** The stage changes while live before/after fingerprints remain equal. `problems` rejects the run, but `outcome` becomes `FAILED`, not the documented `INVALID`.
- **Why tests miss it:** S9 requires only that the outcome is not `OK`.
- **Minimal fix:** Classify source/stage provenance mismatches as `INVALID`; reserve `FAILED` for test failures.

A further retained harness limitation matters for live interpretation: `apply_patch_attack_test.py::stage_b` still calls a turn “completed” using exit/completion/thread presence rather than `runner.turn_problems`. Reader failure, late errors, conflicting identity or truncated evidence can coexist with those checks. Repair this before treating Stage-B completion as a clean-run assertion.

**3. Test adequacy**

The embedded prefix record and raw output agree on **50 outcomes: 36 assertion failures, seven missing-API errors, seven passes, and zero classified behavioral errors**. These remain implementer-recorded results.

The classification is mechanically accurate for exception type, but **“assertion” does not necessarily mean behavioral discrimination**.

| Test area | Recorded pre-fix outcomes | What the tests actually establish |
|---|---|---|
| **S1** | Three assertions fail; pure-debt guard passes | Raw retention and reported settlement failure discriminate old behavior. The pure-debt guard assumes a consistent state and misses T3. |
| **S2** | Three assertion failures | Useful behavioral coverage of durable settlement, reset→reconcile and previously bound fresh records. No late observer coverage. |
| **S3** | Two assertion failures | Exposes stale conversion and reset after unsuccessful conversion. Does not cover ordinary-turn persistence ordering. |
| **S4** | Two assertion failures | One exposes the actual stalled-reader false success. The other fails because `turn_problems` is absent, so it is API-existence discrimination despite its assertion classification. |
| **S5** | Five assertion failures; genuine-write guard passes | Rejects the supplied printed wrappers, simple reads, exact supplied refusal wording and unsupported wrapper logic. It misses T2’s shell-semantic counterexamples. |
| **S6** | Two assertion failures; two guards pass | Contextual output and short JSON-escape regressions are meaningfully detected. Request exclusion and performance guards preserve intended behavior. The long escaped boundary remains untested. |
| **S7** | Two assertion failures | The launch-inspection test behaviorally exposes the unsafe fallback. The first test initially fails because the exception class is absent; it does not reach its behavioral assertion on old source. |
| **S8** | Two assertion failures; escaped-CR guard passes | Reserved-word rejection discriminates. The “raw carriage return” test’s old-code failure occurs at template normalization; its quoted CRLF cases were already rejected because they contain LF. Add a lone raw CR negative. |
| **S9** | Three `api_missing` errors | Old source rejects the new `root`, `evidence` or `staged_after` interfaces before the intended behaviors run. Current functional tests nevertheless provide useful persistent-mutation coverage. |
| **S10** | Three `api_missing` errors; one assertion failure | New prefix-validator/classifier APIs are absent. Only the footer-only accounting test behaviorally distinguishes the old gate. Current chain fixtures remain incomplete. |
| **S11** | Two assertion failures | Strong behavioral discrimination: original refusal masking and unsuccessful fallback reporting. |
| **S12** | Two assertion failures | Strong categorized-error warning coverage. Generic wrapping is omitted. |
| **S13** | Two assertion failures; empty-directory guard passes | First-backup creation and surviving-journal refusal discriminate. Failed witness persistence and usable recovery are omitted. |
| **S14** | Two assertion failures | Both pre-fix failures occur at the missing sender module. They are API-existence assertions, not behavioral comparisons with the former outside sender. The current successful fixture endorses T7. |
| **S15** | Three assertion failures | Meaningfully detects exact-code suppression and exceptional retained-flag reporting. |
| **Also items** | Three assertions fail, one API error, one pass | Discovery union is behavioral; truncation checks expose missing capture metadata; omission-mode testing is structural. Incomplete-MCP refusal stops at the new `Simple` constructor interface. |

The seven declared passes are correctly identified:

1. Pure thread debt remains resettable.
2. Genuine simple attributed writes remain recognized.
3. Request text remains excluded.
4. The scanner remains fast on the specified input.
5. Escaped CR remains CR.
6. Empty state directories do not prove establishment.
7. The selected bare-Git spelling scan remains clean.

Their limits differ. In particular, the genuine-write guard establishes compatibility with its fixtures, not sound attribution; the bare-Git scan is a textual lint guard, not exhaustive launch verification.

The full current `test_review3.py`, `test_remediation.py` and `test_service.py` bodies are now supplied, resolving that previous missing-material item. They contain useful real-process tests against the fake CLI. However:

- `HappyPath.test_prompt_composition_matches_v1` silently omits its actual v1 comparison when that path is absent.
- Older mutation sensitivity has not been re-established.
- Current source tests positively permit missing sender thread identity.
- Changed R13 fixtures now use native structured calls. That is appropriate for testing a supported shape, but does not establish that real rollout wrappers use it.
- A green total cannot supply omitted source bodies or live event-shape evidence.

**4. Evidence**

The revised evidence classes are mostly honest.

- `source_fingerprint.json` now correctly separates source-file observation from executed environment metadata.
- Current CLI observations are labeled implementer-executed and model-free.
- Historical smoke/attack results are correctly excluded as current-source enforcement evidence.
- Integrity evidence honestly records exit 1 for configuration-stat drift. Its cause is unknown; unchanged auth size/mtime does not prove content equality.
- The evidence and snapshot hash scopes are clearly distinguished.

**Read-only staging plus staged/live endpoint fingerprints and a retained log is sufficient for qualified implementer-executed provenance**, assuming the recorder and execution record are trusted. It supports detection of ordinary persistent source changes. It does not establish:

- independent execution authenticity;
- continuous stage immutability;
- detection of modification followed by restoration;
- an immutable import/environment boundary;
- complete prefix-chain validation under the current builder.

The read-only attribute is correctly described as a file attribute. The post-run fingerprint should be described as detecting **persistent differences at that endpoint**, not every change during execution.

The bounded filesystem claim appropriately separates sandbox requests from confidentiality, external capabilities, host-side execution, state/history writes and readiness. It remains overstated where it promises immutable sealed events, universal durable-first ordering, sound smoke attribution or guaranteed first-write backup evidence.

The capability levels are appropriate under their definitions:

| Level | Assessment |
|---|---|
| **resolved** | Appropriate for the CLI’s effective-config observations at audited moments. It does not prove execution loaded that snapshot or that runtime tools disappeared. |
| **requested** | Appropriate for web and sandbox/approval overrides without corresponding effective/runtime evidence. |
| **uncontained** | Appropriate for permitted servers, unexpected enabled-server observations and capabilities without an applied control. |

The discovery/final/later allowed-server union is now source-correct. Calling it an observed configuration exposure is more precise than implying the server was loaded or invoked.

Two universal evidence statements still exceed their observations:

- The empty-table probe establishes that **this override did not disable the observed servers**, not that no supported deny-all mechanism exists.
- The historical rollout establishes **no separate nested apply_patch records in that rollout**, not a universal persistence rule.

Also, retained `features list` text is visibly a clipped tail. Production `load_features` now requires complete capture; that does not make the retained tail a complete effective-state record.

**5. Decisions**

These recommendations are not authorization.

| Decision | Options | Recommendation |
|---|---|---|
| **D1 — Configuration/capability isolation** | Retain per-server overrides; investigate isolated configuration including `--ignore-user-config`; allow reviewed exceptions | Retain the empty default allow-list. Investigate the flag as a separate design change with explicit backend/configuration selection and fresh/resumed evidence. Do not infer its MCP behavior from rejected `mcp list` use. Treat the final-load race as a human risk decision. |
| **D3 — Activation** | Defer; later side-by-side evaluation; cut-over | **Defer activation.** Resolve T1/T2 and the relevant validity/provenance defects before relying on live evaluation results. Prefer side-by-side evaluation later if selected by the human. |
| **D8-web** | Keep requested-disabled; permit research under a defined policy | Keep requested-disabled. Runtime absence remains unverified; exceptions need a human-selected egress policy. |
| **D8-delegation/image** | Keep gates disabled; selectively enable | Keep disabled pending trustworthy runtime evidence. Model self-report is insufficient. |
| **P2 — Service children** | Accept uncertainty; require reconciliation; develop stronger observation | Retain conservative operator reconciliation. Fence late callbacks so that recorded confirmation and settlement cannot be undone. |
| **P3 — Degraded reviews** | Retain removal; diagnostic-only output; restore degraded success | Retain removal. Incomplete or infrastructure-failed evidence should remain diagnostic, with the review withheld. |

Recommended gates for `REVIEW_PACKAGE.md` section 11:

| Proposed check | Findings to resolve before relying on its conclusions |
|---|---|
| Fingerprint/evidence preparation | T8/T11; qualify staging claims and correct T12. |
| Model-free preflight and Stage A | S7 is repaired. Obtain the omitted host-Git/ownership source and define complete-capture handling for oracle evidence. Real denial formatting can then be collected as diagnostic evidence. |
| Live smoke enforcement interpretation | **T2**, including shell semantics, wrapper lexical validity and target-attributed refusal. |
| Stage-B interpretation | T9; use shared turn-validity checks, including T7, rather than exit/completion/thread presence alone. |
| Service/stdio recovery evidence | **T1**, plus T3/T4/T5/T6 and the required omitted lifecycle source. |
| Verification-sender validity | T7 and the scanner completeness gap T5. |

A separately human-authorized diagnostic run may collect missing shapes while remaining explicitly inconclusive. This review approves no run, registration or activation.

**6. Cheapest next checks**

In priority order:

1. **Abandoned observer race, mocked:** Block an already-entered `on_thread` write past the join timeout; settle/reconcile and admit another call; release the observer. Verify it cannot overwrite settlement or the new call’s record. Settles T1.
2. **Smoke forgery negatives, pure functions:** Quoted write mentions, unreachable writes, unrelated read denials, synthesized refusal text, unknown tool names, reserved result names and malformed arrays. Require INCONCLUSIVE. Settles T2.
3. **Ordinary persistence matrix, mocked:** Fail all uncertainty/completion writes while allowing pending; fail observer writes; test final retained-marker failure. Assert actual durable ordering and reported state. Settles T4.
4. **Conflicting marker matrix, mocked:** Mix persistence state with uncertainty reasons and malformed facts. Require refusal rather than destructive disposition. Settles T3.
5. **Oversized boundary tests, pure functions:** Place 1024 `\u0020` escapes across chunk boundaries, including escaped signature letters. Require detection. Settles T5.
6. **Generic-error warnings, mocked:** Raise after preflight/allowed-server warnings and after binding. Require the warning block on the wrapped error. Settles T6.
7. **Shared validity tests, pure functions:** Missing thread ID must invalidate sender and Stage B. Preserve legitimate recoverable pre-completion error behavior. Settles T7.
8. **Evidence-chain negatives, model-free:** Remove overlay entries, alter actual snapshot bytes, falsify expected identities/classifications, and repeat one passing test line. Require rejection. Settles T8/T11.
9. **Lexical and witness checks, mocked:** Test U+0085 between JS tokens and lone quoted CR; fail first backup and journal evidence; exercise missing-identity recovery instructions. Settles T9/T10.
10. **Model-free real-platform diagnostics, if human-selected:** Real junction/link resolution, suspended ownership, clean `findstr` readiness, corrupt-ref behavior and exact per-hunk denial paths.
11. **Freshly human-authorized live evidence:** Capture actual wrapper and failed-command JSON shapes; establish trustworthy capability availability; run repaired fresh/resumed smoke/attack checks; then exercise stdio/service recovery on a disposable repository. Bind each result to source and configuration.

**7. Missing material**

For direct verification of the remaining claims, I need:

- Current bodies omitted as unchanged: `bridge/config.py`, `errors.py`, `fingerprint.py`, `gitsafe.py`, `ids.py`, `jobobject.py`, `telemetry.py`, `server.py`, and the entry points. Their hashes and archived verdicts do not reconstruct their implementations.
- Full current `tests/test_review2.py`, `tests/test_review4.py` and `tests/test_apply_patch_attack.py`; the displayed diffs do not reveal unchanged controls and assertions.
- `tests/fake_codex.py`, `tests/hard_kill_helper.py`, `tools/live_smoke_test.py` and `tools/privacy.py` for complete harness, transport, import and sanitization review.
- This round’s actual `verification_manifest.json`. The embedded manifest is round 4; the builder source cannot establish the exact round-5 file modes or prompt binding.
- The passing run’s raw log if its individual identities, warning interleaving and summary reconciliation are to be assessed directly. It is intentionally hash-listed only.
- Complete retained effective feature readouts and the referenced web schema for direct assessment of those recorded observations.
- The real launcher/package-selection source only if the intended boundary is expanded beyond validated `node.exe` and `codex.js`.
- Current-source runtime evidence for actual shell-wrapper shapes, failed-command events, denial paths, runtime capability availability and service-child outcomes.
- A scripted stdio/service live check with explicit success, refusal, recovery and completeness oracles; section 11 still identifies it as unscripted.

The three test bodies requested last round are now present. The remaining missing material is not supplied by the recorded 380-test total.
