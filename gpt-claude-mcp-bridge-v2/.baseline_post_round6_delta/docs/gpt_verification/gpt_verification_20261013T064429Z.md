<!-- GPT's verification review, verbatim. Advisory; NOT approval. Metadata: request_20261013T064429Z.json -->

This is advisory, not approval of activation, registration, or live checks. No tools were used.

The remediation fixes many of the original paths, but **thread disposition can still be lost, executable selection can still reach repository-controlled code, and the wrapper oracle accepts statements that do not perform the call it attributes**. I recommend deferring activation.

**1. Verdict tables**

“CLOSED” means the specified source defect is addressed. It does not establish live enforcement or independently verify the recorded tests.

### V1–V15

| Finding | Verdict | Claim assessment, source evidence, and remaining work |
|---|---|---|
| **V1 — Runner cleanup** | **PARTIALLY CLOSED** | **CONTRADICTED as an absolute guarantee.** `bridge/runner.py::run_turn` now protects ownership/events construction, starts readers before the writer, joins only recorded started threads, and closes the owner in an outer `finally`. Those repairs are source-visible and the four `V1_RunnerCleanup` tests exercise the original defects. However, allocations still occur after `Popen` and before the cleanup `try`; cleanup polling can also raise and mask the original error. See R10. |
| **V2 — Only durable record deleted** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the repaired conversion branch.** `service.py::_check_session_health` checks pending persistence before clearing `completed_unbound`; `persistence_failed` retries conversion. `V2V3_StaleRecoveryNeverDeletesTheOnlyRecord.test_completed_unbound_survives_a_failed_pending_write_and_keeps_blocking` targets that defect. Other administrative and failed-turn paths still discard thread debt, and failed uncertainty persistence can lose the process fact. See R1–R2. |
| **V3 — Hard owner death** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for stale running-phase records.** `_check_session_health` no longer grants automatic recovery because ownership was `job_object`; the rewritten tests require refusal. The stronger claim that every dead-owner call requires process reconciliation is not upheld by the `completed_unbound` conversion and subsequent adopt/reset workflow. See R2 and design claim A below. |
| **V4 — Telemetry boundary** | **CLOSED** | **CONFIRMED FROM SOURCE.** `service.py::_telemetry_dir/_emit` validate both destinations and skip when neither is safe. `V4_TelemetryNeverEscapesTheStateBoundary.test_home_equal_to_the_repository_writes_nothing_after_the_rejection` exercises the previous fallback defect. Ordinary service/admin state access is preceded by validation in `_execute`, `_context`, `_state_only`, and `cmd_status`. This is path validation, not protection against concurrent filesystem-link substitution. |
| **V5 — Forged wrappers** | **NOT CLOSED** | **CONTRADICTED.** `apply_patch_attack_test.py::parse_wrapper` accepts shadowing result declarations and arguments declared after their use. It therefore does not establish an executed call receiving the decoded patch. `evaluate_turn/attempted_patch/normalise_patch` also compare an extracted, rewritten patch rather than the entire received argument. See R5. The embedded new test module contains no V5 tests; the attack-test diff does not demonstrate coverage of these cases. |
| **V6 — Executable selection** | **NOT CLOSED** | **CONTRADICTED.** `execpath.py::trusted_which` validates the PATH directory, not the final executable target. `codexcli.py::resolve_cli` additionally accepts adjacent Node/package artifacts without target validation. Bare host `git` remains in `live_smoke_test.py::git` and `tests/helpers.py::World.git`. Case-insensitive `GIT_*` scrubbing is confirmed in `gitsafe.py::safe_git_env`. See R3. |
| **V7 — Infrastructure completeness** | **PARTIALLY CLOSED** | **CONTRADICTED as a completeness claim.** The supplied nested-content and middle-of-line defects are repaired in `_gather_text/OversizeScan`, and the named tests exercise them. Traversal still stops at depth eight and globally skips potentially output-bearing `files/path/status` containers. Oversized scanning operates on encoded JSON, and stdout reader failure remains silently tolerated. See R4. |
| **V8 — Recovery commands** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for canonical `persistence_failed` and `uncertain + unbound_thread` records.** `admin.py::cmd_reconcile/_settle_unbound_thread` implement those cases. The tests exercise adoption and reset of persistence-only debt and adoption of combined debt. Raw stale records and interrupted fresh turns with only `thread_id` bypass that separation. See R1. |
| **V9 — Maintenance** | **PARTIALLY CLOSED** | **CONTRADICTED as “every path.”** `state.py::begin_maintenance` refuses an ordinarily readable stale flag; `end_maintenance` releases its lock after unlink failure; `_require_quiescent` handles probe failure. The named tests target those repairs. The new flag-existence check occurs outside the lock-release scope, and callers ignore unsuccessful flag removal. See R7. |
| **V10 — Capability recheck** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the successful/refusal paths tested.** `recheck_mcp`, `Audit.note_allowed_enabled`, and `_locked_call` report later-enabled allowed servers and place the last read after preparation. The remaining configuration-load race is stated honestly. But marker-unlink failure masks the refusal and leaves false inference/history accounting. See R8. `McpRecheck` contains enabled-server partitions, not a complete configuration snapshot. |
| **V11 — OS-denial attribution** | **PARTIALLY CLOSED** | **CONTRADICTED at the exact-target boundary.** `names_write_denial` requires write-failure wording but matches only a basename substring anywhere on the line. A denial concerning a different path can pass. A1/A2 now check add/update/delete effects in `stage_a`; that change is source-visible, but its actual execution is unverified. See R6. |
| **V12 — Fingerprint completeness** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for reader-error and incomplete-HEAD handling.** `_run_capture` records reader exceptions; `_read_head` rejects incomplete reads and uses `HEAD^{commit}`. The tests target those changes. However, a target-ref lookup returning 1 is treated as proof that the ref is absent; malformed/unreadable ref handling is not established by the embedded evidence. Tighten that distinction before claiming verified unborn state. |
| **V13 — Evidence binding** | **PARTIALLY CLOSED** | **CONTRADICTED as the package-wide gate claim.** `cmd_tests` implements matching endpoint fingerprints, exit/skip checks, and retained-log hashing. `evidence_problems` checks those test-record fields, but never validates `integrity.json` or `cli_observations.json` binding and does not reconcile recorded summaries with log contents. Its tests exercise helper/gate predicates, not the complete recording pipeline. See R9. |
| **V14 — Warning visibility** | **PARTIALLY CLOSED** | **CONTRADICTED as the blanket fallback claim.** Current repaired success-path warnings receive text before the answer in `_locked_call`; the named tests establish this for disabled preflight and failed post-turn recheck. But `_BANNERED_ELSEWHERE` suppresses fallback text by prefix, without proving that a banner was rendered. See R12. |
| **V15 — Exact formats** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for full-string identifier validation.** `ids`, admin/state validators, server override validation, and telemetry token matching use `fullmatch`; tests exercise trailing-newline rejection. Newly introduced enum checks throw on list/dictionary values instead of dropping them to `null`, causing the whole telemetry record to be skipped. See R11. |

**Cross-cutting design claims**

| Claim | Assessment |
|---|---|
| **A — No stale record ever auto-recovers; process and thread resolutions remain separate** | **CONTRADICTED.** `_check_session_health` removes dead-owner `completed_unbound` records after creating pending state. Adopt/reset can then clear that pending state without `reconcile`. More seriously, `cmd_reconcile` can directly clear a raw stale record containing a known fresh thread without creating pending state. The separation works only for particular already-converted marker shapes. |
| **B — One resolver never selects from the reviewed tree and covers every relevant executable** | **CONTRADICTED.** Directory-level checks do not validate executable or package targets, adjacent Node bypasses `trusted_which`, and embedded smoke/test scaffolding still launches bare host Git. Absolute System32 addressing is implemented, but depends on an inherited `SystemRoot`/`windir` value that `system_tool` does not validate. |

### Earlier N findings and P3

| Finding | Re-grade | Source evidence and remaining work |
|---|---|---|
| **N1 — Host Git containment** | **NOT CLOSED** | `gitsafe.py::safe_git_argv/safe_git_env` retain helper controls and add absolute selection, but R3 breaks the executable boundary. Bare scaffolding routes remain. The full `N1_HostGitContainment` test body is absent, so the claimed stronger fsmonitor control cannot be inspected. |
| **N2 — State validation** | **CLOSED** | `admin.py::_context/_state_only/cmd_status` and `service.py::_telemetry_dir` address the identified entry-point/fallback bypasses. |
| **N3 — Ownership** | **PARTIALLY CLOSED** | `runner.py::run_turn` retains suspended assignment and conservative interruption handling. R2 leaves gaps in durable process uncertainty; service-owned processes remain outside observation. |
| **N4 — Exceptions/stale recovery** | **PARTIALLY CLOSED** | The original constructor/thread-start cleanup defects are repaired and tested. R10 remains; source-level cleanup is not universal. |
| **N5 — Maintenance** | **PARTIALLY CLOSED** | `Registry.begin_maintenance/end_maintenance` repair normal stale/unlink paths. R7 remains. |
| **N6 — Durable admission/persistence** | **NOT CLOSED** | Mandatory admission and checked pending conversion are implemented. R1–R2 still defeat the promised independent recovery facts. |
| **N7 — Infrastructure evidence** | **PARTIALLY CLOSED** | `_gather_text/OversizeScan` improve coverage. R4 prevents a completeness claim. |
| **N8 — Capability drift** | **PARTIALLY CLOSED** | Late allow-list reporting is repaired. R8 remains, alongside the honestly declared configuration-load race. |
| **N9 — Wrapper oracle** | **NOT CLOSED** | Strict grammar rejects obvious control-flow forgeries, but R5 defeats argument/execution attribution. |
| **N10 — Instance identity** | **PARTIALLY CLOSED** | `Registry._read/instance_state` retain invalid/unreadable-state refusal. Losing both `instance.json` and `registry.json` still allows initialization despite surviving `registry.prev.json` or other established-state evidence. This earlier finding remains unchanged. |
| **N11 — Fingerprint completeness** | **PARTIALLY CLOSED** | Reader failures and separate diff streams are addressed. Ref-absence verification and broader Git visibility limits remain. |
| **N12 — Oracle attribution** | **PARTIALLY CLOSED** | Read-denial negatives and stronger controls improve attribution. R6 remains; no current-source live denial evidence exists. |
| **N13 — Filename probe** | **CLOSED** | `_probe_file/sandbox_preflight` retain unsafe-name exclusion and direct `findstr` arguments. Real-launcher behavior remains unverified, as declared. |
| **N14 — Identifier ingress** | **CLOSED** | `ids.py::is_thread_id`, `Events.feed`, and state/telemetry thread validation now reject the complete malformed string. R11 is a separate telemetry type-handling regression. |
| **N15 — Visible missing evidence** | **CLOSED** | `_locked_call` still visibly reports unavailable consistency and skipped source probing, now before the answer. |
| **P3 — Degraded mode removed** | **CLOSED** | `config.py::load_settings` rejects degraded mode; `_locked_call` withholds answers for detected infrastructure failure. Detection gaps remain N7, rather than restoration of degraded mode. |

### F1–F8

| Finding | Re-grade | Source evidence and remaining work |
|---|---|---|
| **F1 — Actual-turn failures** | **PARTIALLY CLOSED** | `Events/classify_item/_locked_call` address tested shapes. Fix R4 and capture supported-CLI failed-command JSON. |
| **F2 — Capability containment** | **PARTIALLY CLOSED** | Policy requests and snapshot readouts are implemented. Runtime inventory, final-load race, and R8 remain. |
| **F3 — Process lifetime** | **PARTIALLY CLOSED** | Job ownership and ordinary cleanup improve substantially. R2/R10 and service-owned-process limits remain. |
| **F4 — Registry/admin lifecycle** | **PARTIALLY CLOSED** | Validation, identity checks, and ordinary maintenance are implemented. R1/R7 and retained N10 remain. |
| **F5 — Partial/uncertain outcomes** | **NOT CLOSED** | R1–R2 still permit loss or conflation of independent recovery facts. |
| **F6 — Review consistency** | **PARTIALLY CLOSED** | Separate hashes and visible unavailability work. Executable selection, ref verification, and incomplete visibility scope remain. |
| **F7 — Attack-test validity** | **NOT CLOSED** | R5–R6 invalidate the stated exact-attempt/target guarantees. Smoke-test evidence is also weaker than its labels suggest; see R13. |
| **F8 — Evidence/documentation** | **PARTIALLY CLOSED** | Recorded attribution and endpoint provenance improve. R9, remaining wording contradictions, and missing source/test material prevent closure. |

**2. New findings**

No Critical finding is established from the embedded material. Attribution as newly introduced is strongest where the supplied source diff contains the defective code; several retained defects are explicitly identified below.

**R1 — High: Reconciliation can discard thread disposition before health conversion**

**Location:** `bridge/admin.py::cmd_reconcile/_settle_unbound_thread`; `bridge/service.py::_locked_call::settle_failed_turn`.

Two concrete paths:

- A dead bridge leaves `state="running", resumed=False, thread_id=T`, possibly with `phase="completed_unbound"`. The operator runs `reconcile --yes` before another service call. `cmd_reconcile` considers only `unbound_thread` and `persistence_failed`; it clears the record without creating pending state. The next call starts a fresh thread.
- A fresh interrupted turn reports thread T, then times out or fails under process uncertainty. `_locked_call` retains an uncertain marker with `thread_id=T`, but does not add pending state or `unbound_thread`. Reconciliation again removes the only blocking thread-disposition record.

This contradicts the documented meaning of reconciliation as process confirmation only.

**Tests miss it:** Recovery tests first invoke service health conversion, or construct canonical `persistence_failed`/`unbound_thread` states. The timeout fixture sleeps before emitting `thread.started`.

**Minimal fix:** Derive thread debt consistently from fresh/resumed status, phase, pointer state, and observed thread identity in both service and admin paths. Reconcile must durably preserve that debt before clearing process uncertainty. Adoption/reset should recognize the same raw states.

---

**R2 — High: Failed uncertainty persistence can make an interrupted call look persistence-only**

**Location:** `service.py::_locked_call/_check_session_health`; `state.py::Registry.update_marker`.

Concrete sequence:

1. A result has unconfirmed termination.
2. Writing `state="uncertain"` fails; the durable marker remains `running`.
3. The later `update_marker(phase="completed_unbound")` succeeds, reading that old durable record.
4. The bridge dies before completing pointer persistence.
5. Health recovery creates pending state and removes the in-flight marker because its phase is `completed_unbound`.
6. Adopt/reset clears pending state; another turn can start without process reconciliation.

Process uncertainty existed, but was not durably represented in the record used by later transitions.

There is also a separate crash window in stale recovery: pending state is written **before** the uncertain-state update. A crash between those writes leaves `running + pending`, and subsequent health checks report pending before establishing process uncertainty.

**Tests miss it:** They fail pending writes while allowing in-flight updates. They do not inject failure or death between these independent transitions.

**Minimal fix:** Persist independent process and disposition facts together in the primary marker before creating/removing secondary markers. A completed phase must not itself imply reconciled processes. Under declared design A, dead-owner conversion should retain process debt until explicit reconciliation.

---

**R3 — High: Executable resolution validates directories but not execution targets**

**Location:** `execpath.py::trusted_which`; `codexcli.py::resolve_cli`; retained scaffolding in `live_smoke_test.py::git` and `tests/helpers.py::World.git`.

A trusted absolute PATH directory contains `git.exe` or `codex.exe` linked to a file inside the reviewed repository. The directory passes validation, `isfile(full)` follows the link, and the resolver returns the candidate. Repository-controlled executable bytes can run on the host.

Likewise, an accepted npm shim can lead to an adjacent `node.exe` or `codex.js` whose resolved target lies inside an excluded root. Neither derived target is checked.

This is distinct from trusting a compromised executable genuinely outside the project: the promised exclusion explicitly concerns repository-controlled targets.

**Tests miss it:** They plant ordinary files inside excluded directories. They do not test executable-file links, package links, or derived Node targets. The smoke harness still invokes bare Git.

**Minimal fix:** Validate canonical targets for every executable and derived launcher artifact; return validated absolute targets. Apply the resolver to remaining host scaffolding. Validate System32 roots or obtain them through a trusted OS mechanism. Document that path validation still does not control DLL loading or subsequent replacement races.

The bare sandbox `git/cmd/findstr` commands are a separate, declared sandbox-resolution boundary; they are not covered by this host resolver.

---

**R4 — High: Incomplete stdout evidence can still produce an ordinary answer**

**Location:** `runner.py::run_turn::read_stdout`, `_gather_text`, `OversizeScan`.

Concrete cases:

- The reader observes a valid thread and `turn.completed`, then raises while reading subsequent records. Its exception is swallowed. With exit 0 and an answer file, `_verify` can return success despite an unread tail containing an infrastructure failure or post-completion error.
- A recognized failed command places its diagnostic under `result.files[*].error`, or more than eight container levels deep. `_gather_text` drops it.
- An oversized JSON record encodes a signature with Unicode escapes, such as `helper\u005funknown_error`. The raw-byte scan does not see the decoded signature.
- The oversized item-type regex can identify an earlier nested `item` object instead of the event’s actual item and incorrectly apply the prose exclusion.

**Tests miss it:** They cover shallow containers, plain encoded signatures, and a prose type appearing immediately in the actual item. No stdout-reader exception test exists.

**Minimal fix:** Record stdout completeness and refuse a review when capture failed. Traverse output structures without silently dropping output-bearing containers; expose any traversal limit as incomplete evidence. For oversized records, use structurally reliable classification and escape-aware scanning, or report incomplete infrastructure evidence.

---

**R5 — High: The strict wrapper grammar lacks JavaScript binding semantics and exact argument comparison**

**Location:** `apply_patch_attack_test.py::parse_wrapper/decode_js_literal/attempted_patch/normalise_patch/evaluate_turn`.

The parser accepts this shape:

```javascript
const tools = await tools.apply_patch("<expected patch>");
text(tools);
```

Its result variable is not checked for shadowing. In JavaScript, the local `tools` binding shadows the supplied tools object during its own initializer; this statement cannot perform the attributed tool call.

It also accepts:

```javascript
const result = await tools.apply_patch(patch);
var patch = "<expected patch>";
text(result);
```

The parser collects bindings across the entire body and reports the later literal as the call argument. At the call site, the variable has not received that value.

These are concrete false authenticity judgments. I do **not** claim that their actual runtime exceptions necessarily contain policy-rejection wording; that behavior is not embedded.

Separately, a genuine wrapper passing `<expected patch> + trailing junk` can satisfy `evaluate_turn`: `attempted_patch` extracts only the patch substring. `normalise_patch` also converts literal backslash-n sequences into actual line breaks. Thus the oracle’s supplied-input comparison does not establish the exact received argument.

**Tests miss it:** No embedded test checks result-name shadowing, declaration order, whole-argument equality, or literal-backslash normalization. Existing negative tests largely cover constructs already rejected by the previous heuristic.

**Minimal fix:** Validate binding order, reserved identifiers, and shadowing for every declaration, including result declarations. Compare the complete decoded argument under a narrowly specified newline policy. Restrict the accepted subset further rather than guessing at unsupported JavaScript semantics. Preserve `wrapper_attested`.

Declared false rejections—comments, interpolation, unusual genuine wrappers—are acceptable as INCONCLUSIVE. They do not justify accepting semantically invalid members of the claimed grammar.

---

**R6 — Medium: “Target identified” means only a basename substring**

**Location:** `apply_patch_attack_test.py::names_write_denial/judge_os_attack/evaluate_turn`.

For the expected `canary.txt`, this input passes `names_write_denial`:

```text
Failed to write file C:\other\notcanary.txt.bak: Access is denied. (os error 5)
```

The basename appears as a substring, but the denied write concerns another target. Even an exact basename can identify a different directory. Operation wording and a target mention elsewhere on the same line also need not describe the same operation.

**Tests miss it:** They use a wholly different basename or an explicit read failure.

**Minimal fix:** Parse supported denial formats into an operation and target, then compare the normalized full target against the attacked path resolved under the recorded working directory. Unparseable attribution remains INCONCLUSIVE.

---

**R7 — Medium: The newly added maintenance probe can leak the lock; failed clearing is reported as success**

**Location:** `state.py::Registry.begin_maintenance/end_maintenance`; registry-wide admin commands.

After acquiring `maintenance.lock`, `begin_maintenance` calls `maintenance_path.exists()` outside a release-protected scope. If that check raises, the lock is neither released nor stored for later cleanup.

Additionally, `end_maintenance` now returns `False` on removal failure, but its admin callers ignore that result and can report successful completion while maintenance still blocks every call.

**Tests miss it:** They inject unlink and quiescence-probe failures, not failure of the newly added existence check. They test `False` directly without testing command exit/reporting.

**Minimal fix:** Protect every operation after lock acquisition with cleanup. Propagate unsuccessful flag clearing into actionable command output and a nonzero exit status.

---

**R8 — Medium: Late MCP refusal cleanup can create false inference uncertainty**

**Location:** `service.py::_locked_call::abort_before_exec`.

If the final MCP recheck refuses and marker unlink raises, `abort_before_exec` never reaches the assignments setting `inference_may_have_started=False` and `history="no_turn_started"`. The unlink exception masks the original categorized refusal. The session retains a running marker even though no inference subprocess was launched.

**Tests miss it:** The V10 refusal test assumes successful marker deletion.

**Minimal fix:** Correct the known invocation facts before fallible cleanup, preserve the original refusal, and durably distinguish “never started” state if deletion fails. Recovery should not misclassify that marker as an interrupted inference.

---

**R9 — Medium: The prompt gate does not validate all embedded evidence records**

**Location:** `make_verification_prompt.py::evidence_problems`; `record_evidence.py::cmd_tests/cmd_integrity/cmd_cli`.

A stale `integrity.json` or `cli_observations.json` is embedded without any binding check. This directly contradicts “every evidence record is bound to the current source.”

For tests, the gate validates the retained log’s hash but trusts summary fields without checking that the log actually reports the same passing, skip-free run. The gate tests explicitly accept a log containing only `raw output\nOK\n` while claiming five tests.

Matching endpoint fingerprints also cannot establish that files remained unchanged throughout execution: change-and-restore between endpoints is invisible. The documentation should not equate endpoint equality with proven continuous byte identity.

**Tests miss it:** They cover stale test/policy fingerprints and log hashes, not stale integrity/CLI records, contradictory summaries, or a change-and-restore run.

**Minimal fix:** Validate each evidence type and its binding; reconcile test summaries with retained output. Prefer execution from an immutable staged snapshot, and describe endpoint checks accurately. This still remains implementer-executed evidence.

---

**R10 — Medium: A small post-Popen cleanup gap remains**

**Location:** `runner.py::run_turn/_terminate_owned`.

`bytearray()` and list initialization occur after `Popen` but before the protected `try`. An allocation failure there leaves a suspended child outside cleanup.

Also, `_terminate_owned` is documented “never raises,” but its `proc.poll()` is outside exception handling. A polling exception during cleanup can replace the original categorized failure, although the outer owner-close `finally` still runs when an owner exists.

**Tests miss it:** Constructor failures are injected only into `ProcessOwnership` and `Events`; cleanup polling is not fault-injected.

**Minimal fix:** Initialize bookkeeping before process creation and enter the protected scope immediately after successful `Popen`. Make cleanup operations independently best-effort while preserving the primary exception.

---

**R11 — Low: Malformed enum types discard the entire telemetry record**

**Location:** `telemetry.py::sanitise`.

`sanitise({"outcome": []})` reaches membership testing against a `frozenset` with an unhashable list. It raises rather than replacing the invalid field with `None`. `Telemetry.write` catches the error and drops the whole record.

**Tests miss it:** Typed-field tests supply strings only.

**Minimal fix:** Require a string before enum membership; map other types to `None`. Test list/dictionary inputs for every typed field.

---

**R12 — Low: Generic warning fallback relies on a prefix assumption**

**Location:** `service.py::_locked_call`, `_BANNERED_ELSEWHERE`.

A recorded warning such as `state_not_saved:example` receives no generic text if its producer forgets the associated banner. Prefix membership is treated as evidence that rendering already happened.

Current successful call sites appear to render their expected warnings. This finding concerns the stronger fallback contract and the advertised regression protection.

**Tests miss it:** The “forgotten banner” test uses a non-exempt preflight warning. The static append scan also misses variable-based appends.

**Minimal fix:** Track exact warning codes successfully rendered, then render every remaining code generically.

---

**R13 — Medium, retained harness defect: Smoke labels treat narration as execution evidence**

**Location:** `tools/live_smoke_test.py::main`.

The positive control checks whether the answer contains `CONTROL-OK` and the expected HEAD. That does not establish execution of `git rev-parse HEAD`; the model could obtain HEAD through another read or provide that line without the requested command.

The harness also does not verify that write attempts occurred. An unchanged filesystem can result from skipped attempts. The printed model account of available tools is not a runtime tool inventory.

**Tests miss it:** No embedded test exercises those smoke oracles.

**Minimal fix:** Retain actual command/tool records and require observed attempts plus the control result. Label unchanged-filesystem checks separately from enforcement checks. Runtime inventory needs a trustworthy inventory observation, not model self-report.

**3. Test adequacy**

The following assesses assertions visible in the embedded material, not the recorded totals. The pre-fix source lacks APIs imported by the new module, notably `execpath`; an unchanged test run can fail during import rather than expose the intended behavior.

| V finding | Would the visible tests detect the pre-fix defect? | Qualification |
|---|---|---|
| **V1** | **Yes, behaviorally.** | Constructor/start failures and exited-root descendants target the original cleanup defects. They miss R10 and reader completeness. |
| **V2** | **Yes.** | Failed pending migration and running-phase dead-owner refusal would distinguish the old behavior. No partial uncertainty-write/crash matrix. |
| **V3** | **Yes for running-phase recovery.** | Old job-owned automatic recovery violates the refusal assertions. Completed-phase and direct-admin paths are omitted. |
| **V4** | **Yes.** | Home-equals-repository detects the old fallback write. The two ordinary destination tests can correctly pass on old code. |
| **V5** | **Not established for the important forgery.** | Full attack-test bodies are absent; the diff does not demonstrate an unreachable-call test. Existing obvious-forgery tests could pass on old code. The changed expectation for “strict grammar” can fail merely because diagnostic wording changed. |
| **V6** | **Yes for planted directories and absolute argv.** | Some failures would be missing-new-API errors. No linked-target or derived-launcher test. |
| **V7** | **Yes for nested content, middle signatures, and oversized prose.** | The command-text exclusion is correctly an existing-behavior guard. Missing output structures, encoded signatures, and reader failure are untested. |
| **V8** | **Yes for canonical retained-marker workflows.** | New `unbound_thread` lookups can fail before the intended old-code assertion. Direct stale reconciliation and observed-thread interruption are absent. |
| **V9** | **Yes.** | Stale overwrite, unlink lock leak, and quiescence failure are directly exercised. New existence-check and command-reporting defects are absent. |
| **V10** | **Yes.** | Late allowed-server reporting and preparation ordering distinguish the old behavior. Cleanup failure is untested. |
| **V11** | **Yes for unrelated basename/read denials in stage B.** | The visible new negative would fail the old permissive oracle. Full stage-A test bodies and control-effect tests are absent. |
| **V12** | **Yes for reader failure and incomplete HEAD.** | The genuine unborn case remains a behavior guard, although its new harness call can error against old APIs. Corrupt/unreadable symbolic target refs are omitted. |
| **V13** | **Yes as gate/helper failures; incomplete pipeline proof.** | `run_problems` does not exist in the old source. Testing that helper does not establish that `cmd_tests` supplies correct before/after values or derives a reliable result. |
| **V14** | **Yes for disabled preflight and details ordering.** | Forgotten non-exempt warnings and post-turn read failure are covered. Exempt warnings and reaped-descendant text are not. |
| **V15** | **Yes.** | Trailing-newline assertions directly expose `.match` behavior, and made-up string enums expose the old general-token acceptance. Non-string enum values are omitted. |

Specific weak or misleading tests:

- `V8_RetainedMarkerRecovery.test_independent_process_uncertainty_survives_adopt_and_reset` exercises **adopt and reconcile**, not reset.
- `V14_WarningsAreNeverHidden.test_unavailable_post_turn_recheck_and_reaped_descendants_are_bannered` exercises the unavailable recheck, not reaped-descendant rendering.
- `V13_EvidenceBinding.test_the_prompt_gate_checks_after_run_hash_exit_code_skips_and_the_retained_log` accepts a log without the claimed test count; it proves hash checking, not summary consistency.
- The attack-test strict-grammar reason assertion can fail old code for wording alone.
- The older suspended-start sentinel remains after stdin reading in `fake_codex.py::run_exec`; its absence does not prove that startup instructions never executed.
- Structural/source-string tests do not establish stage-A execution or transitive import safety.

The reported **48 failures/errors and three passes** cannot be verified from the package: the pre-fix raw output and the exact overlay/import methodology are missing.

**4. Evidence**

The `implementer_executed` label and separation of historical live results are appropriate. The retained log and matching endpoint hashes materially improve auditability. They are **sufficient to support a qualified implementer-recorded result**, assuming the recorder and supplied records are trusted; they do not independently prove the execution or establish immutable source throughout it.

Corrections needed:

- `source_observation` is defined as “no execution,” but `cmd_cli` executes CLI commands, and fingerprint metadata also invokes the CLI. These should be implementer-executed, model-free observations.
- A historical rollout with zero records named `apply_patch` supports that observation for that rollout. It does not prove that Codex never persists nested records in other configurations or shapes.
- The deny-all probe establishes that **this `mcp_servers={}` override did not disable the listed servers**. It does not establish the universal absence of any supported deny-all approach. The embedded help also exposes `--ignore-user-config`, whose suitability is not examined here.
- Integrity output records an exit-1 configuration-stat change. The app-restart attribution remains an implementer hypothesis, not demonstrated causation. Content equality is correctly disclaimed.
- The prompt gate does not validate every evidence record as claimed.
- The previous reviewed hash and the stated diff-base snapshot hash differ. Different hash scopes could explain that; the missing snapshot manifest/algorithm prevents verification that the base is exactly the reviewed source.

The read-only wording correctly separates argv pinning from unverified native enforcement, external side effects, confidentiality, host execution, and sandbox readiness. It still overstates executable exclusion, recovery separation, wrapper exactness, and infrastructure completeness.

`resolved`, `requested`, and `uncontained` are useful and mostly correctly assigned **under their explicitly snapshot-scoped definitions**:

- Feature/MCP readouts justify snapshot-level `resolved`.
- Web and sandbox/approval requests appropriately remain `requested`.
- Allowed MCP servers appropriately remain `uncontained`.

Two scope corrections remain: `allowed_servers` accumulates observations rather than representing only the last snapshot, and post-turn unexpected enabled servers produce a warning without being added as named uncontained exposures in the capability summary.

The consistency limitation remains understated. In addition to same-size/same-mtime untracked edits, the fingerprint is bounded by Git visibility: ignored files, index visibility flags/stat caching, and nested submodule content are not comprehensively covered.

**5. Decisions**

These are recommendations only.

| Decision | Options | Recommendation |
|---|---|---|
| **D1** | Empty allow-list; explicit exceptions; stronger configuration isolation | Retain the empty default. Fix executable and reporting defects; investigate isolated configuration using supported CLI controls. Treat the remaining race as an explicit human risk decision. |
| **D3** | Defer; side-by-side evaluation; cut-over | **Defer activation.** After remediation and adequate evidence, prefer human-selected side-by-side evaluation with edits paused. |
| **D8-web** | Keep disabled; permit research under an egress policy | Keep disabled. The requested override’s runtime effect remains unverified. |
| **D8-delegation/image** | Keep gates disabled; selectively enable | Keep disabled pending trustworthy runtime availability evidence. |
| **P2** | Accept uncertain service children; operator reconciliation; investigate lifecycle | Retain conservative reconciliation, but make process debt durable and independent of thread debt. Do not infer safety from completed phase alone. |
| **P3** | Keep removal; diagnostic-only path; restore degraded reviews | Retain removal from production review success. |

The requested live-check section is **§11**, not §10, in the supplied package.

Recommended gates:

- **Host/model-free checks:** R3 before use against untrusted repositories; R10 before relying on real-launcher supervision.
- **Smoke/service-path checks:** R1–R2, R4, R8, and R10 before treating the run as lifecycle/contract evidence.
- **Stage-B enforcement interpretation:** R5–R6 first.
- **Smoke enforcement/inventory interpretation:** R13 first.
- **Hash-bound evidence interpretation:** R9 first.

These are evidence and safety recommendations, not authorization. A human could separately authorize a diagnostic run to gather missing behavior, with its outcomes explicitly classified as diagnostic rather than conclusive verification.

**6. Cheapest next checks**

In priority order, none performed here:

1. **Direct-admin recovery matrix:** Run reconcile/adopt/reset on raw stale running and completed records before any service health check. Verify process and thread debts survive independently.
2. **Persistence/crash matrix:** Fail uncertainty writes, then allow phase/pending writes; interrupt between every transition. Assert no second inference without the required resolutions.
3. **Observed-thread interruption:** Emit a thread ID before timeout, cancellation, and runner failure. Verify reconciliation preserves thread disposition.
4. **Resolver links:** Test executable-file links, directory junctions, derived Node/package targets, invalid SystemRoot values, and remaining bare host scaffolding.
5. **Wrapper semantics:** Test shadowed result names, reserved identifiers, bindings declared after use, trailing argument junk, literal backslash-newline sequences, and genuine supported wrappers.
6. **Evidence completeness:** Inject stdout-reader failure after completion; test deep/output-bearing containers, escaped oversized signatures, and misleading nested item types.
7. **Denial attribution:** Test substring basenames, identical basenames in different directories, and mixed read/write diagnostics.
8. **Remaining low-cost failures:** Maintenance existence-check/unlink reporting, MCP-refusal unlink failure, enum list/dictionary values, and exempt warning fallback.
9. **Evidence pipeline:** Exercise `cmd_tests` end to end; reject stale CLI/integrity records and contradictory summaries. Establish a staged immutable execution snapshot.
10. **Model-free real-CLI checks, if human-selected:** Actual launcher ownership, representative `findstr` files, corrupt/unreadable symbolic refs, and per-hunk patch-engine controls with retained attributable outputs.
11. **Freshly human-authorized allowance checks:** Capture actual failed-command JSON and trustworthy capability availability; run corrected fresh/resumed attack oracles; then the complete service/stdio path. Bind each result to its source and configuration.

**7. Missing material**

Needed to settle the remaining review questions:

- Full current and relevant baseline bodies of `tests/test_review2.py` and `tests/test_apply_patch_attack.py`; diffs and test names do not supply their unchanged assertions/helpers.
- Full unchanged `test_server_admin`, `test_state`, and `test_config_argv_auth` bodies where schema, cancellation, lock, and argv equivalence are relied upon.
- Raw pre-fix test output and the exact import/overlay procedure used for the reported 48/51 sensitivity result.
- `tools/privacy.py`, which is invoked by the builder and evidence recorder but not embedded.
- Full `tools/make_telemetry_examples.py` and `tools/make_remediation_diff.py`, plus generated telemetry examples.
- The diff-base `MANIFEST.json` and hash-scope definitions needed to relate `9036cc29…` to the reviewed `07106480…` source.
- This review’s actual verification manifest; only the round-2 manifest is embedded.
- v1 baseline/comparison implementation and manifests, lock contents, and verification implementation if integrity claims are to be independently assessed from source.
- Complete relevant CLI feature/schema observations and underlying historical rollout evidence; the supplied feature output is a truncated tail, and no web config schema is embedded.
- Trusted runtime evidence for failed-command shapes, capability availability, launcher behavior, attributable denials, and service-owned process outcomes. It is explicitly absent for this source.
- The outside-package sender source and checks, if its prompt-hash/binding/no-retry claims are to be reviewed.
