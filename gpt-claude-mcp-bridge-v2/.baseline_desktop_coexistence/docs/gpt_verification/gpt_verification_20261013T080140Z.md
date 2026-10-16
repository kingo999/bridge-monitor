<!-- GPT's verification review, verbatim. Advisory; NOT approval. Metadata: request_20261013T080140Z.json -->

This review is advisory. I recommend deferring activation: the source still permits process uncertainty to be cleared through thread-disposition commands, and the revised smoke oracle can still mistake printed text for execution. No tools were used; recorded executions were not independently verified.

**1. Verdict tables**

“CLOSED” means the specified source defect is repaired within its stated scope. It does not establish live enforcement. Test assessments concern the assertions as written.

| Finding | Verdict | Claim assessment, source evidence, test coverage, remaining work |
|---|---|---|
| **R1 — Thread debt on every path** | **NOT CLOSED** | **CONTRADICTED.** `state.py::thread_debt` centralizes derivation, and `admin.py::cmd_reconcile` preserves a derived debt before deletion. But `_settle_unbound_thread` deletes raw `running` records during reset/adopt, thereby also deleting unresolved process state. Reset of an `uncertain` record merely clears `unbound_thread`; derivation then recreates the debt from `thread_id`. `test_review4.py::R1_ThreadDebtIsDerivedOnEveryPath.test_reset_and_adopt_recognise_a_raw_stale_record_too` explicitly requires the unsafe deletion. Persist independent process and disposition facts, including a durable settled/discarded disposition. |
| **R2 — Durable process fact** | **PARTIALLY CLOSED** | **CONTRADICTED as a durability guarantee.** `service.py::_locked_call` uses whole-record writes and retries the ordinary uncertainty write. However, `_check_session_health` ignores `update_marker`’s Boolean result and can write pending state before process uncertainty actually persists. Raw admin deletion remains possible. The transient-write test catches the original later-phase overwrite; the ordering test checks invocation order, not successful persistence. Check the primary write before proceeding and test persistent failures/crash boundaries. |
| **R3 — Execution targets** | **PARTIALLY CLOSED** | **CONTRADICTED as an unconditional claim.** `execpath.py::trusted_which/trusted_target` check real targets, and `codexcli.py::resolve_cli` checks adjacent Node/script artifacts. But `system_tool` falls back to inherited `SystemRoot`/`windir` when the OS query fails. The real npm launcher’s further executable selection is not embedded. R3 tests exercise successful OS lookup and simulated target rejection, not fallback failure or the complete launcher chain. Fail closed on system-directory lookup failure; examine further derived executable targets. |
| **R4 — Evidence completeness** | **PARTIALLY CLOSED** | **CONTRADICTED as a completeness claim.** `runner.py::_gather_text_ex` reports traversal limits, stdout exceptions set `reader_failed`, and service withholds flagged answers. Yet a reader still alive after its bounded join is not marked incomplete. Nested output fields named `input`/`arguments` are still skipped globally. Oversized scanning does not decode ordinary JSON whitespace escapes. R4 tests discriminate several repaired cases but omit these paths. Require reader EOF/completion and scan output using contextual key handling and adequate JSON decoding. |
| **R5 — Wrapper semantics** | **PARTIALLY CLOSED** | **CONTRADICTED as a sound grammar claim.** `apply_patch_attack_test.py::parse_wrapper/_resolve_arg/patch_matches` repair binding order, specified shadowing, and whole-argument comparison. But `_IDENT` accepts JavaScript keywords, and `_STRLIT` accepts raw carriage returns in quoted strings. The parser can therefore attest wrappers that cannot execute. R5 tests cover the original examples, not lexical validity. Reject invalid identifiers/literals or reduce the accepted grammar further. |
| **R6 — Exact denial target** | **CLOSED** | **CONFIRMED FROM SOURCE within lexical path comparison.** `names_write_denial/_canonical_path` compare the parsed full path under a recorded base; missing/unparseable attribution does not pass. R6 tests distinguish basename substrings, other directories, reads, and outside targets. Actual engine path formats, aliases and extended UNC spelling remain unverified; unsupported forms should remain INCONCLUSIVE. |
| **R7 — Maintenance** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the original lock-release repair; CONTRADICTED as universal reporting.** `Registry.begin_maintenance` protects the existence check. Normal admin completion reports unsuccessful removal. If the administrative operation raises, execution skips the reporting after `finally`; `_require_quiescent` also ignores unsuccessful clearing. R7 tests cover existence-check failure and successful-action reporting only. Report retained flags on exceptional paths while preserving the original error. |
| **R8 — Refusal cleanup** | **PARTIALLY CLOSED** | **CONTRADICTED as guaranteed durable marking.** `service.py::_locked_call::abort_before_exec` corrects invocation facts first and guards deletion. It ignores failure to write `refused_before_exec`. The separate spawn/ownership-refusal branch still clears the marker before correcting facts and can mask its original refusal. The R8 test permits the fallback write and exercises only late MCP refusal. Cover unsuccessful fallback persistence and reuse the guarded refusal cleanup across all proven-no-execution paths. |
| **R9 — Evidence gate/provenance** | **PARTIALLY CLOSED** | **CONTRADICTED as an immutable-source and complete-binding guarantee.** `record_evidence.py::cmd_tests` stages source; `evidence_problems/log_problems` check current record bindings and passing-log summaries. The stage remains writable and is not fingerprinted after execution. Prefix-log binding and actual baseline overlay contents are not validated. R9 tests cover predicates and source strings, not recorder execution or these gaps. Strengthen staged execution and validate prefix evidence end to end. |
| **R10 — Cleanup gaps** | **CLOSED** | **CONFIRMED FROM SOURCE for the specified defects.** `runner.py::run_turn` creates bookkeeping before `Popen`; `_terminate_owned/_still_running` guard polling. The polling test behaviorally exposes the old masking defect; the allocation-order test is structural. Reader completion is a separate remaining R4 issue. |
| **R11 — Typed telemetry** | **CLOSED** | **CONFIRMED FROM SOURCE.** `telemetry.py::sanitise` checks string type before enum membership. `R11_TypedTelemetryFields.test_non_string_values_never_raise_and_never_drop_the_record` exercises lists, dictionaries and other malformed types. |
| **R12 — Exact rendered codes** | **PARTIALLY CLOSED** | **CONTRADICTED as “no prefix exemption.”** The final fallback uses exact membership, but `service.py::_locked_call` populates `bannered` using `startswith("state_not_saved:")` whenever any persistence banner exists. Error exits also bypass warning rendering. Unexpected post-turn servers are correctly added by `Audit.note_unexpected_enabled`. R12 tests cover a different prefix and successful answers only. Register the actual rendered persistence code and preserve warnings on errors. |
| **R13 — Smoke oracles** | **NOT CLOSED** | **CONTRADICTED.** `smoke_oracles.py::control_executed/probe_steps` inspect recorded call text, but infer execution from substrings without validating the invoked operation or wrapper. Printing a command/target and refusal can satisfy them. R13 tests exclude answer narration but omit forged tool-call bodies. Require attributable execution records or strict validated wrappers; otherwise report INCONCLUSIVE. The inventory self-report labeling is correctly repaired. |

| V finding | Re-grade | Source/test evidence and remaining work |
|---|---|---|
| **V1 — Runner cleanup** | **CLOSED** | `runner.py::run_turn/_terminate_owned/_still_running` repair the specified setup/allocation/polling defects. R10 tests cover polling and ordering. This does not settle stdout completeness. |
| **V2 — Only durable record deleted** | **PARTIALLY CLOSED** | `_check_session_health` preserves canonical persistence debt, but `admin.py::_settle_unbound_thread` deletes raw process records. R1/R2 remain. |
| **V3 — Hard owner death** | **NOT CLOSED** | Service health refuses dead-owner records in every phase, but direct reset/adopt can delete a raw record and permit inference without reconciliation. The new raw-admin test endorses this bypass. |
| **V4 — Telemetry boundary** | **CLOSED** | `service.py::_telemetry_dir/_emit` validate configured and fallback destinations. Closure remains path-scoped; concurrent link replacement is not controlled. |
| **V5 — Forged wrappers** | **PARTIALLY CLOSED** | `parse_wrapper/patch_matches` fix the original forgeries and argument extraction. Invalid JavaScript can still be attested; add lexical negatives. |
| **V6 — Executable selection** | **PARTIALLY CLOSED** | `trusted_which`, `trusted_target`, and shim-artifact checks repair target links. `system_tool` retains an unsafe fallback; the further npm launcher chain needs examination. |
| **V7 — Infrastructure completeness** | **PARTIALLY CLOSED** | `_gather_text_ex`, `OversizeScan`, and the reader-failure flag improve coverage. Reader-join, contextual output keys and JSON-escape gaps remain. |
| **V8 — Recovery commands** | **NOT CLOSED** | `cmd_reconcile` preserves derived debt, but `_settle_unbound_thread/thread_debt` do not implement independent, durable settlement across raw records and reset. |
| **V9 — Maintenance** | **PARTIALLY CLOSED** | `begin_maintenance/end_maintenance` repair lock release. Exceptional admin paths can omit retained-flag reporting. |
| **V10 — Capability recheck** | **PARTIALLY CLOSED** | `recheck_mcp`, allowed-server accumulation, and post-turn unexpected exposure reporting are implemented. No-execution cleanup remains incomplete; configuration-load races remain explicitly unresolved. |
| **V11 — OS-denial attribution** | **CLOSED** | `names_write_denial` and R6 negative tests repair basename/read-denial attribution. Closure concerns supported parsed messages, not live enforcement. |
| **V12 — Fingerprint completeness** | **CLOSED** | `_read_head` now requires `show-ref --verify --quiet` exit 1 for UNBORN; the retained V12 test rejects exit 128. Actual corrupt-ref behavior still needs model-free confirmation. |
| **V13 — Evidence binding** | **PARTIALLY CLOSED** | `cmd_tests/evidence_problems/log_problems` improve source/log binding. Writable staging, prefix evidence and incomplete pipeline tests remain. |
| **V14 — Warning visibility** | **PARTIALLY CLOSED** | Success-path rendering is stronger, but persistence-prefix marking and errors still bypass the blanket visibility contract. |
| **V15 — Exact formats** | **CLOSED** | `ids.py::is_thread_id`, state/config validators and `telemetry.py::sanitise` use full-string/type checks. R11 closes the malformed-enum regression. |

| N finding / P3 | Re-grade | Source/test evidence and remaining work |
|---|---|---|
| **N1 — Host Git containment** | **PARTIALLY CLOSED** | `gitsafe.py::safe_git_argv/safe_git_env` retain helper controls; executable target checks improve selection. Complete launcher/system-tool containment remains incomplete. `N1_HostGitContainment` requires textconv and clean controls to fire, **not fsmonitor**, so its control is weaker than the README’s description. |
| **N2 — State validation** | **CLOSED** | `admin.py::_context/_state_only/cmd_status` and service telemetry validation address the entry-point bypasses; `N2_StateDirValidationEverywhere` exercises rejected-directory non-access. |
| **N3 — Ownership** | **PARTIALLY CLOSED** | Suspended assignment and job cleanup remain implemented in runner/jobobject. Recovery still permits loss of process debt; service children remain unobservable. |
| **N4 — Exceptions/stale recovery** | **PARTIALLY CLOSED** | R10 repairs cleanup masking, and owner liveness stays conservative. Direct admin recovery still bypasses stale process reconciliation. |
| **N5 — Maintenance** | **PARTIALLY CLOSED** | Exclusive maintenance and protected existence checks are implemented. Exceptional retained-flag reporting remains incomplete. |
| **N6 — Durable admission/persistence** | **NOT CLOSED** | Admission is checked in `_locked_call`; independent recovery facts are still lost or resurrected through R1/R2 paths. |
| **N7 — Infrastructure evidence** | **PARTIALLY CLOSED** | R4 tests cover reader exceptions, deep structures and escaped underscores. Remaining reader/scan gaps prevent completeness. |
| **N8 — Capability drift** | **PARTIALLY CLOSED** | Rechecks and named exposures are source-visible and tested. Error-path visibility and no-execution cleanup remain incomplete; the race remains. |
| **N9 — Wrapper oracle** | **PARTIALLY CLOSED** | Exact argument and original shadow/order repairs are implemented. JavaScript lexical validity remains unsound. |
| **N10 — Instance identity** | **PARTIALLY CLOSED** | `Registry._read` correctly recognizes a surviving backup. After only one successful call, no backup need exist; losing registry and instance still permits initialization despite surviving lifecycle journal evidence. The new test covers two calls only. |
| **N11 — Fingerprint completeness** | **PARTIALLY CLOSED** | Separate staged/worktree hashes, incomplete-read handling and stricter UNBORN logic are implemented. Git visibility and non-atomic sampling remain bounded limitations. |
| **N12 — Oracle attribution** | **PARTIALLY CLOSED** | Full-path parsing and separate stage-A hunks improve attribution. Real engine formats/controls remain unverified for this source, and smoke attribution remains unsound. |
| **N13 — Filename probe** | **CLOSED** | `_probe_file/sandbox_preflight` retain unsafe-name rejection and direct `findstr` data arguments; source tests exercise this. Real-launcher readiness remains unverified. |
| **N14 — Identifier ingress** | **CLOSED** | `Events.feed/is_thread_id` reject malformed complete identifiers before callbacks/storage; N14 tests exercise rejection and valid flow. |
| **N15 — Visible missing evidence** | **CLOSED** | Successful answers visibly report unavailable fingerprints and skipped probes in `_locked_call`; N15 tests exercise both. Broader error-warning visibility remains R12. |
| **P3 — Degraded mode removed** | **CLOSED** | `config.py::load_settings` rejects degraded mode; detected infrastructure/incomplete-evidence failures withhold answers. Detection gaps do not restore degraded mode. |

| F finding | Re-grade | Source evidence and remaining work |
|---|---|---|
| **F1 — Actual-turn failures** | **PARTIALLY CLOSED** | `Events/classify_item_ex/_locked_call` reject tested failures. Complete reader handling and supported-CLI failed-command captures remain necessary. |
| **F2 — Capability containment** | **PARTIALLY CLOSED** | Snapshot controls and reporting are implemented. Runtime availability, final-load race, complete readout handling and error-path reporting remain unresolved. |
| **F3 — Process lifetime** | **NOT CLOSED** | Job ownership/cleanup are improved, but raw reset/adopt can remove unresolved process state. Service-child lifetime remains an operator fact. |
| **F4 — Registry/admin lifecycle** | **PARTIALLY CLOSED** | Identity, backup-loss and maintenance repairs are implemented. Recovery transitions, first-call identity loss and exceptional reporting remain. |
| **F5 — Partial/uncertain outcomes** | **NOT CLOSED** | Process and disposition settlement are still not independent across all administrative paths. |
| **F6 — Review consistency** | **PARTIALLY CLOSED** | Fingerprint code and its documented Git visibility bounds are stronger. Sampling is not an atomic snapshot, and complete executable-chain behavior is unverified. |
| **F7 — Attack-test validity** | **NOT CLOSED** | Exact patch/target comparison improves stage A/B. Smoke oracles still permit false execution attribution; wrapper lexical validity remains incomplete. |
| **F8 — Evidence/documentation** | **PARTIALLY CLOSED** | Attribution, retained records and hash scopes improve. Immutable-stage claims, prefix binding, sender validation and several wording claims remain overstated. |

The four cross-cutting claims have these assessments:

| Claim | Assessment |
|---|---|
| **A — No dead-owner auto-recovery; process fact durable first** | **CONTRADICTED.** `admin.py::_settle_unbound_thread` deletes raw records; `service.py::_check_session_health` ignores primary-write failure before mirroring debt. |
| **B — Independent facts and resolutions through one derivation** | **CONTRADICTED.** The shared derivation exists, but reset/adopt transitions neither preserve raw process uncertainty nor durably record discarded disposition. |
| **C — Real targets and OS-derived system tools** | **CONTRADICTED as unconditional.** File/artifact checks exist, but `system_tool` has an inherited-environment fallback; the complete npm launcher chain cannot be verified from the embedded material. |
| **D — Unreadable evidence never becomes a review** | **CONTRADICTED.** A still-running stdout reader is not treated as incomplete. `codexcli.py::run_simple` also silently tolerates reader failures and capture truncation for configuration evidence. |

**2. New findings**

No Critical finding is established. “Introduced” below refers to source-visible changes; inherited gaps are identified.

**S1 — High: Raw reset/adopt settles process uncertainty without reconciliation**

- **Location:** `bridge/admin.py::_settle_unbound_thread`, `cmd_reset`, `cmd_adopt`.
- **Scenario:** A bridge dies leaving `state="running", resumed=False, thread_id=T`. Reset or adopt runs before service health conversion. The helper considers process uncertainty present only for literal state `uncertain`, so it deletes the raw record. Adoption permits a resumed call immediately; reset permits a fresh call.
- **Tests:** The new `R1_ThreadDebtIsDerivedOnEveryPath.test_reset_and_adopt_recognise_a_raw_stale_record_too` requires exactly this unsafe behavior. Thus this is a regression assertion pointing in the wrong direction.
- **Minimal fix:** Treat an unsettled raw running record as independent process debt. Disposition commands must retain it until explicit reconciliation, regardless of whether service health ran first.

**S2 — High: Reset does not durably settle a derived thread debt**

- **Location:** `state.py::thread_debt`; `admin.py::_settle_unbound_thread/cmd_reset`.
- **Scenario:** An uncertain fresh call records T. Reset clears pending state and sets `unbound_thread=None`, but retains `thread_id=T` and `resumed=False`. The next health check or reconcile derives T again and recreates disposition debt. A previously bound uncertain fresh call has the same problem after reset retires its pointer; `pointer_bound` is ignored.
- **Tests:** The declared R2 reset guard checks only that explicit `unbound_thread` is null and process refusal remains. It does not subsequently reconcile and verify that the discarded debt stays discarded.
- **Minimal fix:** Persist a disposition status such as discarded/settled, and make derivation honor it. Keep observed thread identity separately for audit.

**S3 — High: “Primary durable first” checks call order, not persistence**

- **Location:** `service.py::_check_session_health`; `state.py::Registry.update_marker`.
- **Scenario:** Updating a raw record to `uncertain` returns `False`; writing pending succeeds. The service proceeds as though the primary update persisted. A following reset can clear pending and delete the still-running primary through S1.
- **Tests:** `test_recovery_makes_the_process_fact_durable_before_the_thread_debt` records invocation order while allowing both writes. The transient uncertainty-write test exercises a different branch.
- **Minimal fix:** Require successful primary persistence before mirroring pending state. On failure, retain the raw record, refuse, and report the persistence failure. Inject unsuccessful writes and crashes between transitions.

**S4 — High: A reader that has not finished can still support success**

- **Location:** `runner.py::run_turn` cleanup joins; `service.py::_locked_call`.
- **Scenario:** Stdout has delivered T and `turn.completed`, then processing/read blocks before a later error. The root exits and an answer file exists. The five-second join expires, but `reader_failed` remains false and no EOF/completeness flag is checked. Service can bind and return the answer before the tail is inspected.
- **Tests:** R4 injects an exception, which sets the new flag. It does not keep the reader alive past the join timeout.
- **Minimal fix:** Record successful EOF and require the stdout reader to have completed before returning usable evidence. A join timeout must yield incomplete evidence. Avoid exposing event objects that are still being mutated.

**S5 — High: Smoke “recorded execution” is still forgeable through call text**

- **Location:** `tools/smoke_oracles.py::control_executed/probe_steps`.
- **Scenario:** A recorded `exec` body such as `text("git rev-parse HEAD <actual HEAD>")` satisfies the control matcher if its output contains HEAD. Likewise, printing `"canary.txt TAMPERED rejected"` can satisfy the append attempt/refusal matcher without attempting a write. A read operation mentioning a target and returning permission denial can also be misclassified as a denied write.
- **Tests:** R13 tests use simple faithful command strings. They exclude final-answer narration, but never test forged wrappers, read denials or unrelated refusals.
- **Minimal fix:** Parse actual execution arguments and attribute the output to the exact operation. For wrapper-only records, apply an explicit attestation grammar; unsupported shapes must be INCONCLUSIVE.

**S6 — Medium: Output traversal and oversized JSON still have silent blind spots**

- **Location:** `runner.py::_gather_text_ex`, `OversizeScan`.
- **Scenario:** A failed tool returns `result={"input":{"error":"helper_unknown_error"}}`; global `_INPUT_KEYS` skips the diagnostic although it is inside returned output. An oversized diagnostic containing `CreateProcess\n{` as JSON text is encoded with `\\n`; the scanner does not convert that escape into whitespace for its signature regex.
- **Tests:** Output-key tests use `files/path/status_detail/kind`, not request-like names inside output. Escape tests cover only `\u005f`.
- **Minimal fix:** Skip request fields at known request positions, not recursively throughout returned data. Decode JSON string escapes adequately, or mark unsupported oversized evidence incomplete. Also bound or stream-match the unbounded whitespace in `CreateProcess\s*\{`.

**S7 — Medium: System-tool fallback reinstates the rejected trust source**

- **Location:** `execpath.py::system_tool`.
- **Scenario:** `GetSystemDirectoryW` fails, raises or returns an unusable length; an inherited `SystemRoot=C:\Evil` then selects `C:\Evil\System32\taskkill.exe` or `tasklist.exe`.
- **Tests:** The R3 system-tool test leaves the OS query operational.
- **Minimal fix:** Fail closed when the trusted OS lookup fails. Do not fall back to inherited paths. Separately, validate any further executable artifacts selected by the real npm launcher before claiming complete target containment.

**S8 — Medium: The wrapper grammar accepts invalid JavaScript**

- **Location:** `apply_patch_attack_test.py::_IDENT/_STRLIT`, `parse_wrapper`, `decode_js_literal`.
- **Scenario:** `const null = "<patch>"; const r = await tools.apply_patch(null); text(r);` passes the parser’s binding model but is invalid JavaScript. Quoted literals containing raw carriage returns are also accepted; their decoded CRLF patch can pass `patch_matches`, although the quoted JavaScript cannot execute.
- **Tests:** R5 covers selected reserved names and declaration ordering, not language keywords or raw line terminators.
- **Minimal fix:** Validate identifiers against JavaScript lexical restrictions and quoted literals against line-terminator rules, or support a smaller fixed grammar. This establishes false authenticity judgments; the embedded material does **not** establish that these runtime errors would themselves produce policy-rejection output.

**S9 — Medium: Staging is isolation from live edits, not immutability**

- **Location:** `record_evidence.py::cmd_tests`.
- **Scenario:** Tests or another process modify a staged module before it is imported. Only the pre-execution stage fingerprint is retained; matching live before/after hashes do not detect the change. Cleanup removes the stage before any post-execution fingerprint.
- **Tests:** The recorder test searches for source strings. It does not run the pipeline with a staged-file mutation.
- **Minimal fix:** Make the staged source read-only for the test run and retain a post-run staged fingerprint before deletion. Describe residual environment/import trust separately; a post-run hash alone still does not prove continuous immutability.

**S10 — Medium: Prefix evidence is embedded without validating its binding chain**

- **Location:** `prefix_check.py::main`; `make_verification_prompt.py::evidence_problems`.
- **Scenario:** Prefix JSON retains the current source hash, but its log is replaced or the baseline source copied into the overlay differs from its manifest. The builder checks only the prefix JSON’s source field and a nonempty log’s presence. It does not check the prefix raw-log hash, reconcile outcomes, or verify copied baseline files.
- **Tests:** R9 changes prefix JSON’s source hash only. The prefix runner can also return success for a nonempty outcome set consisting entirely of loader errors.
- **Minimal fix:** Verify the baseline overlay against manifest hashes, bind current harness bytes, validate the prefix log hash/outcomes, and require the intended test count with no loader failures or skips. Keep API errors distinct from behavioral discrimination.

**S11 — Medium: No-execution cleanup remains inconsistent**

- **Location:** `service.py::_locked_call::abort_before_exec` and runner-refusal exception branch.
- **Scenario:** Late refusal deletion fails and writing `refused_before_exec` returns false; the durable record still says running. Separately, deletion after `spawn_failed` or `ownership_unavailable` raises before context facts are corrected, masking the original refusal and recording false history uncertainty.
- **Tests:** The R8 test exercises only late MCP refusal with a successful fallback write; N6’s spawn-failure test assumes deletion succeeds.
- **Minimal fix:** Share one guarded, facts-first cleanup routine for all proven-no-execution paths. Check fallback persistence and expose failure without replacing the refusal.

**S12 — Medium: Error exits hide caller-relevant capability warnings**

- **Location:** `service.py::_locked_call/execute`.
- **Scenario:** The post-turn recheck finds an unexpected enabled server and records a named warning. `_verify`, incomplete-evidence handling or infrastructure handling then raises. The warning renderer is never reached, so the caller’s error omits the exposure while telemetry contains it.
- **Tests:** R12’s exposure test returns a successful answer. No combined exposure-plus-error test is supplied.
- **Minimal fix:** Render relevant accumulated warnings with categorized errors as well as successful answers.

**S13 — Medium, retained: One-call registry loss still looks like first initialization**

- **Location:** `state.py::Registry._read`.
- **Scenario:** One successful call establishes the registry but creates no `registry.prev.json`. Registry and instance files disappear; `journal.jsonl` still contains the successful lifecycle. `_read` returns an empty registry and permits a replacement thread.
- **Tests:** The new N10 loss test deliberately performs two calls, ensuring a backup exists.
- **Minimal fix:** Persist and validate independent establishment evidence, or recognize trustworthy surviving established-state evidence. Do not treat ordinary empty lock directories as proof of establishment.

**S14 — Medium: The supplied sender bypasses repaired review-validity checks**

- **Location:** `docs/gpt_verification/send_verification_source.py.txt::main`.
- **Scenario:** Exit 0, completion and an answer exist, but the stdout reader failed after completion, traversal was incomplete, a post-completion error occurred, or thread identity conflicted. The sender’s `valid` predicate can still mark the review valid because these conditions are not checked.
- **Tests:** No sender-validity tests are embedded.
- **Minimal fix:** Factor and reuse the service’s applicable validity checks, including complete evidence and stable identity. This is an advisory review of the supplied script, not a claim that this failure happened in a recorded send.

**S15 — Low: Prefix-based warning suppression survives in a different form**

- **Location:** `service.py::_locked_call` persistence-banner rendering.
- **Scenario:** A forgotten `state_not_saved:example` warning coexists with a real persistence failure. Rendering one persistence banner marks every matching prefix as bannered, suppressing the forgotten code.
- **Tests:** The new forgotten-warning test uses `child_termination_unconfirmed_extra` and has no persistence failure.
- **Minimal fix:** Add only the actual persistence warning code represented by that banner. Also report retained maintenance flags when an admin action raises; current reporting follows the successful-action path only.

**3. Test adequacy**

The embedded prefix record and raw log agree on **40 tests: 25 assertion failures, 13 errors, two passes**. These are implementer-recorded results.

| Area | Recorded pre-fix outcomes | What that establishes |
|---|---|---|
| **R1** | Three failures, one missing-API error | Interrupted-thread and direct-reconcile assertions expose old behavior. The raw reset/adopt test, however, requires deletion of process debt and is unsafe as written. |
| **R2** | Three failures, one declared pass | Later-phase overwrite, completed-phase recovery and invocation ordering distinguish old source. Successful persistence and durable reset settlement are not established. |
| **R3** | Three failures, one missing-API error | Directory-to-target rejection, selected bare-Git removal and normal system-directory lookup discriminate. The artifact test errors before testing old behavior because `trusted_target` is absent. |
| **R4** | Four failures, one missing-API error | Reader exception after completion, escaped underscores, output keys and misleading nested item types distinguish old behavior. Traversal testing stops at the absent new API on old source. |
| **R5** | One failure, two API/signature errors | The shadow/order test exposes old parser behavior, but stops at its first failing case. Whole-argument negatives do not run on old source because `repo_root` is unsupported. |
| **R6** | Two signature errors | These show interface change, not an old oracle behaviorally accepting the wrong path. Current assertions are useful negatives once the API exists. |
| **R7** | One failure, one behavioral error | `maintenance_busy` after the injected existence-check failure directly demonstrates the old lock leak. It is stronger evidence than a missing-API error. |
| **R8** | One failure | Directly exposes the original masked MCP refusal and wrong invocation facts. |
| **R9** | Four failures | Three predicate tests distinguish gate changes; recorder staging is checked only through source strings. |
| **R10** | One structural failure, one behavioral error | The poll traceback directly demonstrates error masking. The allocation test verifies source ordering only. |
| **R11** | One behavioral error | The unhashable-list traceback directly demonstrates the old telemetry defect. |
| **R12** | Two failures | Detects the old fallback exemption and absent unexpected-server classification. It misses the surviving persistence-prefix path and errors. |
| **R13** | Three import errors | They establish that `smoke_oracles` is new, not that old harness behavior was behaviorally falsified. Current pure tests omit forged recorded calls. |
| **Retained N10/V12** | Two failures; genuine-initialization pass | Backup-loss and mocked exit-128 assertions discriminate. Genuine initialization is a legitimate guard. |

The two declared passing guards need different interpretations:

- **Genuine first initialization:** correctly preserves intended behavior.
- **Reset keeps process uncertainty:** verifies that narrow fact, but passes without checking whether discarded disposition is subsequently regenerated. It is insufficient for independent settlement.

Other weaknesses visible in the tests:

- `R4...test_the_item_type_is_read_only_from_the_events_own_item` contains `assertIsNone(expression and None)`, which is a tautology. Its later type-equality assertion is meaningful.
- R3’s “no bare Git” test scans only four files and one literal spelling. Bare launches remain in the embedded sender and older test controls. Some are deliberate controls, but the universal claim is unsupported.
- `N1_HostGitContainment` requires textconv and clean to fire, not fsmonitor. Its all-controls-removed mutation cannot independently establish fsmonitor sensitivity.
- R9’s “five tests” fixture contains one per-test line. It proves summary/footer comparison, not individual test accounting.
- The R13 harness source assertion does not prove operation attribution.
- Full current `test_review3`, `test_remediation` and `test_service` bodies are not embedded. Their diffs cannot supply unchanged assertions; the prior review is not a substitute for those bodies.

The changes to old completed-phase expectations reflect a legitimate stronger process policy. The new raw reset/adopt expectation conflicts with that same policy and should be reversed.

**4. Evidence**

The evidence classes are mostly more honest, but several qualifications remain.

- Executed CLI observations are now correctly labeled `implementer_executed`, with a model-free kind. However, `cmd_fingerprint` stores `environment()` results from executed CLI commands inside a `source_observation` record. Split that metadata’s class or label the mixed record accordingly.
- The historical rollout establishes **zero separately named apply_patch records in that rollout**. It does not establish a universal persistence rule.
- The deny-all record still says “no supported deny-all.” Its observation establishes only that this override left these listed servers enabled. Broader absence remains unverified.
- `--ignore-user-config` is present in embedded exec/resume help and its rejection by `mcp list` is recorded. Its actual execution configuration effect remains unknown.
- The integrity record honestly shows exit 1 for configuration-stat drift. App restart as its cause is not established. Unchanged auth size/mtime is not content equality.
- The baseline manifest now explains the differing hash scopes. Its listed source hashes provide a coherent documentary relationship to the round-3 manifest; I have not independently recomputed either hash.

Staged execution plus matching fingerprints and a retained log supports **qualified implementer-executed provenance**, assuming the supplied recorder and records are trusted. It isolates executed source from ordinary live-tree edits. It does not presently prove that the writable stage stayed unchanged, that every import came from it, or that execution actually occurred as recorded. The shared environment limitation is appropriately disclosed.

The bounded read-only claim correctly separates argv requests from native enforcement, confidentiality, external capabilities, state/history writes and sandbox readiness. It is still overstated where it asserts universal recovery separation, immutable staging, complete evidence, unconditional system-tool resolution and trustworthy smoke execution attribution.

The evidence levels are appropriate under their expressly limited definitions:

- **Resolved:** effective-config observations at audited moments.
- **Requested:** web and sandbox/approval settings without an effective runtime readout.
- **Uncontained:** allowed or unexpected servers and capabilities without an applied control.

`Audit.note_allowed_enabled` correctly accumulates final-audit and later observations. It does **not** accumulate an allowed server seen only in the initial discovery list and disabled before the final audit, despite its “ANY readout” comment. Describe the actual accumulation scope.

`note_unexpected_enabled` correctly records named post-turn enabled servers as uncontained observations. This does not establish that execution loaded or invoked them; “observed possible exposure” is the appropriate interpretation. Error-path caller visibility remains S12.

`codexcli.py::run_simple` also needs completeness accounting before strong fail-closed readout claims: reader exceptions, still-running drain threads and capture limits are currently silent.

Claims that v1 was never changed, nothing was registered, and no current-source live call occurred are **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL** as universal historical facts. The supplied records and source support narrower statements; they cannot independently establish an absence of all other actions.

**5. Decisions**

These are options and recommendations, not authorization.

| Decision | Options | Recommendation |
|---|---|---|
| **D1 — Capability/configuration isolation** | Retain per-server overrides; use reviewed exceptions; investigate stronger configuration isolation, including `--ignore-user-config` | Retain the empty default. Do not silently adopt the flag: it changes the evidence basis and needs an explicit configuration/backend design plus fresh/resumed runtime evidence. Treat the final-load race as an unresolved human risk decision. |
| **D3 — Activation** | Defer; later side-by-side evaluation; cut-over | **Defer activation.** Resolve the High recovery/completeness defects and evidence-oracle defects before relying on evaluation results. If later selected by the human, prefer side-by-side evaluation with edits paused. |
| **D8-web** | Keep requested-disabled; permit research under an egress policy | Keep requested-disabled and do not describe runtime absence as verified. Any exception needs a human-selected policy. |
| **D8-delegation/image** | Keep gates off; selectively enable | Keep gates off pending trustworthy runtime availability evidence. A model self-report does not settle this. |
| **P2 — Service children** | Accept uncertainty; require operator reconciliation; investigate stronger lifecycle observation | Retain conservative reconciliation. Make it survive raw reset/adopt and unsuccessful state writes. |
| **P3 — Degraded reviews** | Retain removal; diagnostic-only reporting; restore degraded success | Retain removal. Diagnostic output should not become a completed repository review when infrastructure evidence is incomplete. |

Recommended gates for **REVIEW_PACKAGE.md §11**:

- **Fingerprint/evidence preparation:** S9–S10 before claiming immutable staged provenance or validated prefix sensitivity.
- **Model-free launcher/preflight checks:** S7 and complete launcher-chain examination before use against untrusted repositories.
- **Smoke enforcement interpretation:** S5 and S8 before treating attempted/refused steps as conclusive evidence.
- **Stage-B interpretation:** S8 before wrapper authenticity is relied on; retain attributable real denial formats for R6.
- **Service/stdio lifecycle evidence:** S1–S4, S11–S12 before treating a live run as proof of recovery and completeness.
- **Verification-sender validity:** S14 before its `valid_review` field is relied upon.

A separately human-authorized diagnostic run could collect missing behavior while explicitly remaining inconclusive. This review authorizes no such run.

**6. Cheapest next checks**

In priority order, none performed here:

1. **Raw recovery matrix:** Reset/adopt/reconcile before any health conversion, for running and completed phases, fresh/resumed calls and bound/unbound pointers. Settle process and disposition independently.
2. **Settlement persistence:** Reset an uncertain fresh thread, then reconcile and call again. Verify that discarded debt stays discarded. Include `pointer_bound=True`.
3. **Write-failure/crash matrix:** Fail primary uncertainty writes while allowing pending writes; interrupt between transitions. Confirm no route starts inference before required resolutions.
4. **Reader completion:** Keep stdout processing alive beyond the join timeout after completion. Inject nested returned `input/arguments` diagnostics and standard JSON whitespace escapes.
5. **Oracle forgery negatives:** Recorded `text(...)` for control and refusal, read denials, unrelated refusals, unreachable commands and batched outputs. Require INCONCLUSIVE.
6. **Wrapper lexical negatives:** Keywords as identifiers, raw CR in quoted literals, supported escape forms and genuine accepted wrappers. Distinguish false authenticity from conservative rejection.
7. **Cleanup/reporting combinations:** Spawn/ownership refusal plus unlink failure; late refusal plus failed fallback write; unexpected server plus turn error; persistence warning plus forgotten prefixed warning.
8. **Evidence pipeline execution:** Mutate staged source, replace prefix logs, mismatch overlay files, supply loader errors and unexpected test counts. Exercise recorder/builder functions end to end.
9. **First-call identity loss:** Delete registry and instance after one successful call while retaining journal evidence. Confirm refusal.
10. **Model-free real-platform checks, if human-selected:** OS-directory lookup failure, real file links/junctions, further npm launcher targets, actual corrupt-ref exit behavior, `findstr` readiness and attributable per-hunk denial formats.
11. **Freshly human-authorized live checks:** Capture failed-command JSON and trustworthy capability availability; run corrected fresh/resumed attacks and smoke oracles; then exercise the service/stdio path and interruption recovery. Bind each result to source and configuration.

**7. Missing material**

Needed to settle remaining questions beyond the embedded source:

- Full current and relevant baseline bodies of `tests/test_review3.py`, `tests/test_remediation.py` and `tests/test_service.py`; only current diffs are supplied.
- This round’s actual `verification_manifest.json`, including its complete embedded/listed file hashes and prompt hash.
- The passing run’s raw log, if its contents and summary reconciliation are to be assessed directly; it is intentionally hash-listed only.
- Actual baseline overlay bytes or a verified overlay fingerprint, and post-execution staged-source evidence.
- The real installed `codex.js` and any package/native executable-selection code needed to assess the complete launcher chain.
- Complete effective feature observations and the referenced web configuration schema; the embedded feature observation is a truncated tail.
- The historical rollout content needed to assess wrapper/output structure beyond summarized counts.
- Generated current telemetry examples and the v1 baseline manifest if their specific retained contents are to be checked.
- Current-source runtime evidence for failed-command shapes, real launcher/link behavior, attributable denial formats, capability availability, backend behavior and service-child outcomes.
- A scripted service/stdio live check with defined oracles; §11 identifies it as not yet scripted.

None of those missing items is supplied by the green test total alone.
