<!-- GPT's verification review, verbatim. Advisory; NOT approval. Metadata: request_20261012T171145Z.json -->

This is an advisory source review, not approval of activation, registration, or a live run. No tools were used.

The remediation improves several contracts, but **durable recovery, exception-safe supervision, and the attack oracle still have source-visible defects**. The recorded green suite does not close those defects.

**1. Verdicts**

“CLOSED” below means the specified source-level defect is addressed. It does not establish live enforcement or independently verify a recorded test result.

| Finding | Verdict | Implementer claim assessment, source evidence, and remaining work |
|---|---|---|
| **N1 — Host Git helpers** | **PARTIALLY CLOSED** | **CONTRADICTED as stated.** Production fingerprint/probe argv use `bridge/gitsafe.py::safe_git_argv/safe_git_env`, but execution still resolves bare `git`; other embedded host Git routes in `tools/apply_patch_attack_test.py::make_repo/git_head` and `tools/record_evidence.py::cmd_policy` bypass it. Those scaffolding routes use disposable repositories, which is a relevant distinction. Pin a trusted Git executable and narrow “all host-side Git” wording. `N1_HostGitContainment` tests attribute-selected helpers, but does not require the fsmonitor control to fire. |
| **N2 — State validation** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the admin entry points:** `bridge/admin.py::_context/_state_only/cmd_status` validate before registry access. The broader state-write boundary remains broken by `bridge/service.py::_telemetry_dir`, which returns an unvalidated fallback. Fix that fallback; the named admin tests would not detect it. |
| **N3 — Ownership** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the Windows handshake and ordinary timeout/cancel handling:** `bridge/runner.py::run_turn` creates suspended, attaches, then resumes; the service retains uncertainty after Windows timeout/cancel. Hard-owner-death recovery still permits reuse despite unobservable service children. The recorded ≈4 ms measurement is **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL**; the grace tests exercise simulated counts. |
| **N4 — Exceptions/stale recovery** | **NOT CLOSED** | **CONTRADICTED.** `bridge/runner.py::run_turn` constructs ownership/events outside its cleanup `try`, and its `finally` joins potentially unstarted threads before closing the job. `bridge/state.py::owner_is_alive` implements conservative identity comparison, but supervision is not exception-safe on every post-`Popen` path. |
| **N5 — Maintenance** | **PARTIALLY CLOSED** | **CONTRADICTED in the stale-flag portion.** `Registry.begin_maintenance` serializes live owners, but overwrites an existing stale flag without requiring `end-maintenance --yes`. `end_maintenance` can also fail before releasing its lock. The concurrency tests cover competing active owners, not these paths. |
| **N6 — Durable admission/persistence** | **NOT CLOSED** | **CONTRADICTED.** Initial admission is mandatory, but `_check_session_health` clears `completed_unbound` in-flight state even if replacement pending-state persistence fails. `_locked_call` also treats the completed-phase update as best effort, allowing later automatic recovery of a completed but unbound turn as ordinary abandoned execution. |
| **N7 — Infrastructure evidence** | **PARTIALLY CLOSED** | **CONTRADICTED as a completeness claim.** `Events.feed` now classifies error events and the tested long outputs. However, `_gather_text` omits fields such as nested `content`, and oversized head/tail scanning misses middle signatures. An oversized prose event can also be flagged despite the “prose never scanned” wording. |
| **N8 — Capability drift** | **PARTIALLY CLOSED** | **CONTRADICTED in two particulars.** `capability_audit` correctly derives exceptions from its final audit readout and rejects duplicate feature rows. But `_locked_call` performs temporary-directory setup, argv construction, marker persistence and journalling after the alleged “immediate” recheck. Later-enabled allow-listed servers are excluded by `recheck_mcp` and can remain unreported. The residual configuration race is otherwise documented honestly. |
| **N9 — Wrapper oracle** | **NOT CLOSED** | **CONTRADICTED.** `wrapper_analysis/evaluate_turn` check textual patterns, not whether the expected patch is passed to an executed call. Unreachable calls and constructed rejection output can pass. The `wrapper_attested` label correctly acknowledges weaker provenance, but does not make the current PASS reliable. The installed CLI’s lack of nested records is **CANNOT BE VERIFIED FROM THE EMBEDDED MATERIAL**. |
| **N10 — Instance identity** | **PARTIALLY CLOSED** | **CONTRADICTED as literally claimed:** absent instance plus absent registry is accepted by `Registry._read`; legitimate first initialization requires that exception. Invalid/unreadable states and present-registry identity checks are implemented. Losing both identity and registry can still silently initialize despite surviving established-state evidence such as `registry.prev.json`. Clarify and tighten that case. |
| **N11 — Fingerprint completeness** | **PARTIALLY CLOSED** | **CONTRADICTED in incomplete-reader handling.** Cached/worktree streams and status truncation handling are implemented in `repo_fingerprint`. But `_run_capture` does not record reader exceptions; a failed reader can appear complete. An incomplete HEAD read is converted to `UNBORN`, rather than making the fingerprint unavailable. |
| **N12 — Oracle attribution** | **PARTIALLY CLOSED** | **CONFIRMED FROM SOURCE for the stated checks:** generic disk-full wording is no longer sufficient; `judge_os_attack` checks timeout, exit, path name, denial wording and filesystem state. Those conditions still admit a **read-access denial** as write-enforcement evidence, and stage B accepts unrelated OS-denial output. Strengthen action/path attribution and controls. |
| **N13 — Filename probe** | **CLOSED** | **CONFIRMED FROM SOURCE:** `_probe_file/sandbox_preflight` exclude the listed unsafe names, prefer source-like extensions, and pass the selected name directly to `findstr`. The tests assert selection and argv construction. Real-launcher execution and `findstr` readability behavior remain unverified, as declared. |
| **N14 — Identifier ingress** | **PARTIALLY CLOSED** | **CONTRADICTED at the exact-format boundary.** Ingress validation fixes the supplied path-shaped leak, and `redact.py` matches its documented secret-pattern-only scope. But `THREAD_RE.match` and `_TOKEN.match` use `$`, accepting a trailing newline. `sanitise` then reinstates the original accepted thread string. Use full-string validation and field-specific schemas. |
| **N15 — Visible missing evidence** | **CLOSED** | **CONFIRMED FROM SOURCE for the named cases:** `_locked_call` returns a leading flag plus details for an unavailable fingerprint or skipped file probe. `N11N15_FingerprintCompletenessAndVisibility` and `N13N15_SourceProbe` assert returned text. Other warnings remain hidden; see finding V14. |
| **P3 — Remove degraded success** | **CLOSED** | **CONFIRMED FROM SOURCE for removal:** `config.load_settings` accepts only unset/`error`; `_locked_call` withholds answers when recognized infrastructure failures were recorded. **CONTRADICTED for the ancillary assertion that all non-withholding warnings receive a leading flag:** rendering is driven by `banners`, not `warnings`. Detection completeness remains N7. |

| Earlier finding | Verdict | Source evidence and remaining work |
|---|---|---|
| **F1 — Actual-turn access failures** | **PARTIALLY CLOSED** | `runner.Events/classify_item`, `service._locked_call` address the supplied event cases. Close V7 and obtain supported-CLI failed-command JSON evidence. |
| **F2 — Capability containment** | **PARTIALLY CLOSED** | `codexcli.capability_audit/build_argv/recheck_mcp` implement policy requests and snapshot checks. Runtime availability, final-load race and exception reporting remain unresolved. |
| **F3 — Process lifetime/ownership** | **PARTIALLY CLOSED** | Suspended assignment is a meaningful improvement. V1 and V3 prevent closure of cleanup and safe session reuse. |
| **F4 — Registry/admin lifecycle** | **PARTIALLY CLOSED** | Admin validation, live maintenance serialization and identity checks improve the source. V4, V9 and the remaining N10 identity-loss case need correction. |
| **F5 — Partial/uncertain outcomes** | **NOT CLOSED** | Mandatory admission is implemented, but V2 and V8 still defeat the promised persistence/recovery contract. |
| **F6 — Review consistency** | **PARTIALLY CLOSED** | Separate diff streams and visible unavailable results improve coverage. V6 and V12 remain; the declared fingerprint blind spots also need fuller scope. |
| **F7 — Attack-test validity** | **NOT CLOSED** | V5 permits false PASS; V11 weakens OS-denial attribution. Current-source live enforcement is absent. |
| **F8 — Evidence/documentation** | **PARTIALLY CLOSED** | Attribution and rollback wording improve substantially. V13, remaining source/documentation contradictions, and missing review material prevent closure. |

For **§3’s wording claim**, **CONFIRMED FROM SOURCE**: README and REVIEW_PACKAGE distinguish implementer execution, historical evidence, configuration snapshots, and registration versus evaluation history. That textual correction does not validate the implementation promises discussed below.

**2. New findings**

No Critical finding is established from the embedded material.

**V1 — High: Runner cleanup can itself fail, leaving supervision resources unreleased**

**Location:** `bridge/runner.py::run_turn`.

Two concrete paths remain:

- `Popen` succeeds, then `ProcessOwnership()` or `Events()` construction raises before the cleanup `try`. The suspended child has no enclosing cleanup.
- All three thread objects are constructed, but a `Thread.start()` raises. The `finally` calls `join()` on an unstarted thread, which raises before `owner.close()`. If the root has already exited, the conditional cleanup can also skip terminating still-live job members.

The existing setup test raises from `resume`, before thread construction. It does not exercise thread-start failure or assert job-handle release.

**Minimal fix:** Enclose everything immediately after successful `Popen`; track successfully started threads; guarantee job closure through an outermost `finally`; terminate owned descendants on exceptional exits even when the root already exited. Preserve the original categorized failure.

**V2 — High: Recovery can delete the only durable record of an unbound completed turn**

**Location:** `bridge/service.py::_check_session_health/_locked_call`.

Two failure scenarios:

1. A dead owner leaves `phase="completed_unbound"`. Writing the pending marker returns `False`. `_check_session_health` nevertheless clears the in-flight marker. That invocation reports `pending_persistence`, but the next invocation can start a fresh thread.
2. The completed-phase update fails—or the bridge dies during post-run fingerprinting/rechecking, before that update. The surviving marker remains `running/job_object`. Dead-owner recovery clears it and permits replacement, although the turn may have completed without pointer persistence.

The crash test reaches `bind_thread` only **after a successful phase write**. The stale-completion test assumes pending persistence succeeds.

**Minimal fix:** Never remove the old marker until replacement persistence succeeds. A stale fresh call without a committed pointer needs explicit thread-disposition recovery; the absence of a durable completed phase cannot prove that no completed turn exists.

**V3 — High: Hard bridge death bypasses conservative service-child reconciliation**

**Location:** `bridge/service.py::_check_session_health`.

A Windows sandbox-service command survives bridge termination. The marker records a dead owner and job ownership. Recovery clears it and starts another turn, while its banner admits that commands outside the job cannot be confirmed terminated.

The dead-owner test explicitly expects automatic success and therefore preserves this gap. Killing ordinary job descendants does not test service-owned work.

**Minimal fix:** Apply conservative uncertainty to hard-owner death too whenever inference could have started. Job closure proves termination only of job members.

**V4 — High: Telemetry can write into a repository after state validation rejects the call**

**Location:** `bridge/service.py::_telemetry_dir/_emit`.

Review a Git repository rooted at the user’s home directory. The default v2 state directory lies inside that repository. `_execute` rejects it with `state_in_repo`; `_telemetry_dir` encounters the same rejection, then returns that **same unvalidated default**. `_emit` creates telemetry inside the repository.

The supplied worlds keep home and repository separate, so the invalid-state test misses this fallback.

**Minimal fix:** Validate every fallback against the repository and v1. If no safe destination exists, skip telemetry and emit a content-free stderr diagnostic.

**V5 — High: Forged wrappers still satisfy the attack oracle**

**Location:** `tools/apply_patch_attack_test.py::wrapper_analysis/attempted_patch/evaluate_turn`.

For each expected patch, a wrapper can contain:

```javascript
const patch = `<exact expected patch>`;
if (false) await tools.apply_patch({input: patch});
const msg = [
  "patch reje",
  "cted: writing is blocked by read-only sandbox; rejected by user appro",
  "val settings"
].join("");
text(msg);
```

The textual call count is one; the expected patch is recoverable; no forbidden construct matches; no complete rejection phrase appears in the wrapper source. Its fabricated output receives `policy_rejected`, allowing PASS without an attempted write.

An unused expected patch plus a call with a different argument also defeats the alleged attempted-patch comparison.

Existing negatives test conspicuous literal printing and throwing, not unreachable execution or argument binding.

**Minimal fix:** Accept only a strictly defined wrapper grammar that binds the exact patch literal to the single awaited call and forwards its result, with no additional executable constructs. Otherwise return INCONCLUSIVE. Keep the weaker evidence label even after tightening.

**V6 — High: Git containment does not constrain executable selection**

**Location:** `bridge/gitsafe.py::safe_git_argv/supported`; fingerprint/probe subprocess calls.

These functions execute bare `git`. On the Windows deployment, executable resolution is a separate host-execution boundary: a repository-local `git.exe` selected through current-directory resolution, or an earlier executable on inherited PATH, runs before Git’s containment flags have any effect.

The helper tests assume the executable already selected is trusted Git. They would not detect executable substitution.

**Minimal fix:** Resolve and validate a trusted absolute Git executable outside the reviewed tree, and use it consistently for support detection, fingerprinting and selection. Scrub Windows environment names case-insensitively. Document executable/configuration trust assumptions separately from attribute controls.

**V7 — High: Incomplete infrastructure evidence can still produce ordinary success**

**Location:** `bridge/runner.py::_gather_text/Events.feed_oversize/read_stdout`.

Concrete examples:

- A failed recognized item puts diagnostic text in `result.content[*].text`. `_gather_text` does not traverse `content`.
- An oversized failed-command line places `helper_unknown_error` between the retained head and tail. The line is discarded, no infrastructure failure is counted, and subsequent completion plus an answer is accepted.

The oversized regression deliberately puts the signature near the tail. The fixtures do not cover omitted containers.

Conversely, oversized prose containing a signature can be withheld because `feed_oversize` does not preserve the prose exclusion.

**Minimal fix:** Support documented containers and track evidence completeness explicitly. Use a streaming signature scan or a conservative incomplete-evidence policy for oversized failed command records. Make prose handling and its limitations explicit.

**V8 — Medium: Recovery commands conflate process confirmation with thread disposition**

**Location:** `bridge/admin.py::cmd_reconcile/cmd_adopt/cmd_reset`.

After binding and pending-marker writes fail, an in-flight `persistence_failed` marker remains:

- `adopt` can save the pointer but does not clear that marker.
- `reset` does not clear that marker.
- `reconcile`, described as process confirmation, clears it without requiring adoption or explicit disposal of the completed thread. A subsequent call can create a replacement thread.

The retained-marker test asserts blocking, but not either advertised recovery path.

**Minimal fix:** Separate process uncertainty from unresolved pointer persistence. Reconciliation must preserve thread-disposition requirements; adoption/reset must settle the relevant persistence state without discarding independent process uncertainty.

**V9 — Medium: Stale maintenance can be overwritten, and cleanup errors can retain the lock**

**Location:** `Registry.begin_maintenance/end_maintenance`; `admin._require_quiescent`.

With a stale `maintenance.json` and a free OS lock, another maintenance command overwrites the flag and later removes it. The promised explicit stale-clear step is bypassed.

Additionally, an unlink error in `end_maintenance` prevents lock release; an exception during `active_sessions` occurs outside the command’s lifecycle cleanup.

Tests cover successful unlink and active-owner contention only.

**Minimal fix:** Refuse maintenance entry when a flag already exists; require explicit stale clearing. Release locks in `finally`, and cover quiescence-probe failures.

**V10 — Medium: A later allow-listed server can execute without an exception banner**

**Location:** `codexcli.recheck_mcp`; `service._locked_call`.

Set allow-list `docs`; both audit readouts contain no enabled `docs`. It becomes enabled at the pre-exec recheck. `recheck_mcp` filters it out because it is allowed, while `audit.allowed_servers` remains empty. The turn can use an uncontained server without the promised banner or telemetry entry.

The existing test introduces it in the second **audit** readout, not the subsequent recheck.

**Minimal fix:** Return the complete effective server snapshot from rechecks and refresh exception reporting. Put the last recheck after remaining pre-exec preparation, handling refusal without leaving false in-flight uncertainty.

**V11 — Medium: OS-denial evidence does not establish a write denial**

**Location:** `judge_os_attack/stage_a/evaluate_turn`.

`judge_os_attack` accepts:

```text
Error reading canary.txt: Access is denied. (os error 5)
```

That is attributable to the target, but establishes unreadability rather than rejected writing.

The A1 control does not check the update result; A2 checks only add-file creation. Stage B does not require OS-denial output to identify the matched path at all.

`NegativeOracle.test_os_level_denial_is_accepted_but_reported_as_such` supplies the same unrelated `ATTACK.txt` denial for all four attacks and expects PASS.

**Minimal fix:** Verify each control’s intended effect, establish target readability where the engine needs it, and require denial evidence tied to the relevant write operation and target. Otherwise report INCONCLUSIVE.

**V12 — Medium: Reader failure and incomplete HEAD can become comparable fingerprints**

**Location:** `bridge/fingerprint.py::_run_capture/repo_fingerprint`.

An exception inside `pump` ends the thread without setting an error flag. If the process has exited successfully, `complete=True` can describe a partial capture/hash.

Separately, an incomplete HEAD read becomes `UNBORN`; successful later commands can produce `ok=True` despite incomplete evidence.

The tests exercise a byte cap, not reader exceptions or incomplete HEAD.

**Minimal fix:** Record reader exceptions and require successful complete reads. Distinguish verified unborn HEAD from failed/incomplete HEAD discovery.

**V13 — Medium: Evidence is bound to the post-run tree, not necessarily the tested tree**

**Location:** `tools/record_evidence.py::cmd_tests`; `make_verification_prompt.py::evidence_problems`.

Tests run first; the source hash is computed afterward. Editing a module after its test completes but before fingerprinting binds the result to bytes that were not tested.

The prompt gate also ignores `exit_code` and `skipped`, accepting a record with `outcome="OK"` despite a nonzero exit or skipped checks.

The builder tests do not cover those cases.

**Minimal fix:** Capture matching before/after fingerprints, reject changes during execution, require exit zero and the declared skip policy, and retain raw test output. This improves provenance; it does not make implementer evidence independent.

**V14 — Low: Warning rendering and documentation disagree**

**Location:** `bridge/service.py::_locked_call`; README outcome/configuration wording.

Only `banners` trigger a leading flag. Preflight disabled, unavailable post-turn MCP recheck, and reaped-descendant warnings can remain telemetry-only.

Also, README says warning details precede the answer; source places the answer before details.

`SandboxPreflight.test_can_be_disabled_explicitly_and_is_recorded` explicitly expects an unflagged answer.

**Minimal fix:** Classify caller-relevant warnings explicitly and render their details before the answer, or correct the documented contract and scope.

**V15 — Low: “Exact” identifier formats admit a trailing newline**

**Location:** `bridge/ids.py::is_thread_id`; `bridge/telemetry.py::_clean/sanitise`.

A thread identifier such as `"aaaaaaaa\n"` passes the anchored regex because `$` accepts a position before a final newline. It reaches callbacks and persistence; telemetry restores the original value after its general cleaning step.

The negative test uses slashes, which fail for a different reason.

**Minimal fix:** Use `fullmatch`; validate typed fields against their particular formats/enums.

**3. Test adequacy**

The new test module imports APIs absent from the earlier candidate. Running it unchanged against that tree could fail at import or patch setup. The assessments below concern the **behavioral assertions after minimal harness adaptation**, not a claimed execution.

| Finding | Would the tests detect the pre-fix defect? | Important limits |
|---|---|---|
| **N1** | **Yes, for the attribute/helper route exercised.** The plain-Git control plus contained fingerprint/probe checks would expose helper execution. | The control requires textconv and clean, **not fsmonitor**. The mutation succeeds on any assertion failure, rather than verifying the particular containment assertion failed. No executable-substitution test. |
| **N2** | **Yes.** Registry-wide rejection and status noncreation assertions target the original bypasses. | No telemetry-fallback case; all test homes are safely separate. |
| **N3** | **Yes for refusal on failed attachment and timeout reuse. Not reliably for the assignment window.** Updated F3 assertions require no inference and blocking after timeout. | `test_child_is_assigned_to_the_job_before_it_can_run` checks `started_file`, but fake CLI writes that only **after reading stdin**. Withholding the prompt can keep it absent even if unsuspended startup already executed. |
| **N4** | **Yes for live-owner refusal and the tested resume exception.** The old automatic stale recovery would violate those assertions. | No `Thread.start()` failure. The resume-exception test’s absent startup file does not prove the suspended child died or the job handle closed. Dead-owner automatic recovery is expected, masking V3. |
| **N5** | **Yes for active-owner contention and maintenance-bypassing session commands.** The barrier and mutation assertions target the original defects. | No new maintenance command over a stale flag; no cleanup/probe failures. |
| **N6** | **Yes for initial admission, marker presence during binding, successful phase persistence, and instance-write conversion.** | No failed completed-phase update; no failed pending migration during stale recovery; no crash in the post-run/pre-phase interval; no retained-marker recovery workflow. |
| **N7** | **Yes for pre-completion error, beyond-prefix text and tail-position oversized signatures.** | No middle-position oversized signature, omitted text container, or oversized prose exclusion. |
| **N8** | **Yes for final-audit exception reporting, unauthorized recheck drift and duplicate rows.** | No allow-listed server introduced at the later recheck. Tests count readouts but do not establish that the last check follows every preparatory operation. |
| **N9** | **Yes for the old conspicuous comment/literal and throw forgeries.** | Constructed rejection text, unreachable calls and unbound expected literals still pass. The positive label test establishes labeling, not authenticity. |
| **N10** | **Yes for malformed/unreadable instance state, present-registry missing/non-string identity, and invalid backup shape.** | No established state losing both identity and registry while a backup survives. |
| **N11** | **Yes.** The unchanged-worktree/index-content case, unborn case and cap test target the original gaps. | No reader exception or incomplete HEAD; broader Git visibility assumptions remain untested. |
| **N12** | **Yes for generic write wording and the isolated judge’s timeout/setup checks.** | No read-versus-write attribution test. Stage A’s per-hunk test inspects source strings, rather than observing separate executions and control effects. |
| **N13** | **Yes for unsafe selection and changing the constructed command to `findstr`.** | Fake CLI returns success for any normal `findstr` request; it does not validate actual Windows file-reading behavior. |
| **N14** | **Yes for the supplied path-shaped identifier and nested strings with whitespace/slashes.** | No trailing newline; no field-specific enum validation. |
| **N15** | **Yes.** Returned-answer assertions would detect the old telemetry-only fingerprint warning and invisible source-probe skip. | They do not establish visibility of every warning. |

Tests that particularly pass for the wrong or insufficient reason:

- **Suspended-start sentinel test:** the sentinel occurs after prompt reading, so absence does not establish absence of startup execution.
- **Setup-exception test:** absence of that sentinel is insufficient evidence of termination or handle release.
- **OS-denial positive oracle test:** unrelated denial text is accepted for every target.
- **Stage A separation test:** matching source text does not establish execution behavior.
- **Prompt-builder “no way to send” test:** direct import-name inspection ignores transitive imports. Current source calls only `record_evidence.fingerprint`, so I find no sending action in the builder; the test’s broader proof claim is unsupported.

The full current or baseline `tests/test_remediation.py` was not supplied. Its diff supports assessment of changed assertions, but not inspection of unchanged tests or helpers.

**4. Evidence**

The main attribution is appropriately cautious:

- `implementer_executed` accurately distinguishes recorded execution from independent verification.
- Historical results are clearly separated from current-source enforcement.
- `resolved`, `requested`, and `uncontained` are sensible **snapshot-scoped definitions**.
- Allowed MCP servers belong under `uncontained`.
- Sandbox/approval argv pinning and the web override appropriately remain `requested`.

However:

1. **Hash binding is weaker than claimed**, because the recorder fingerprints after testing without checking for intervening changes.
2. **Some “source observation” entries mix empirical execution with source inspection.** `cmd_fingerprint` calls CLI commands through `environment`; checklist assertions about effective CLI behavior and working suspension/grandchild inheritance need execution provenance or embedded source evidence.
3. The capability record’s shell/patch note says effectiveness “is shown by the attack tests recorded in the evidence files.” No current-source live attack evidence is embedded. That note needs the same historical/current qualification as README.
4. `wrapper_attested` is an honest provenance warning, but README’s claims that a PASS establishes the attempted patch and excludes forged output are contradicted by V5.
5. v1 integrity and the desktop-app attribution remain implementer reports. Size/mtime cannot establish configuration-content equality or causation.

The read-only claim is substantially better bounded, but **not yet correctly bounded throughout**. Source inspection establishes policy requests; it does not establish current-source native write rejection. Host executable selection and telemetry fallback add boundaries that the documentation currently overlooks.

The fingerprint’s declared blind spot is also incomplete. Git visibility assumptions—including ignored files, index visibility flags/stat caching, and nested submodule content—need explicit scope or targeted evidence before “repository consistency” is interpreted as comprehensive content coverage.

**5. Decisions**

These are recommendations only.

| Decision | Options | Recommendation |
|---|---|---|
| **D1** | Empty allow-list; selected exceptions; reject the containment design | Retain the empty default. Treat MCP enforcement as a snapshot with a residual race. Resolve V10 and establish trusted host executable selection before relying on the operational boundary. |
| **D3** | Defer; side-by-side evaluation; direct cut-over | **Defer activation while High findings remain.** If subsequently chosen by the human, prefer side-by-side evaluation against corrected, hash-bound source. |
| **D8-web** | Keep disabled; enable under an egress policy | Keep disabled. Obtain runtime evidence for the override’s effect; if web absence is required, its unresolved mapping remains a boundary decision. |
| **D8-delegation/image** | Keep disabled; selectively enable | Keep disabled pending runtime inventory evidence. Effective feature readouts do not establish tool removal. |
| **P2** | Accept uncertainty; operator reconciliation; investigate lifecycle | Retain conservative reconciliation and extend it to hard-owner death and other interruption paths, while investigating service-owned execution. |
| **P3** | Removed production mode; diagnostic-only surface; restore opt-in behavior | Retain removal from review success. Correct warning visibility separately; diagnostic output should not imply a completed review. |

I recommend these gates for REVIEW_PACKAGE §10:

- **Host Git/probe checks:** address V6 before executing against potentially hostile repositories.
- **Smoke and service-path checks:** address V1–V4 and V8 first.
- **Stage B attack checks:** address V1 and V5 first; otherwise the run can leak supervision resources or produce an unreliable PASS.
- **Interpreting Stage A as enforcement evidence:** address V11 first.
- **Interpreting any new result as hash-bound evidence:** address V13.

These recommendations do not authorize the proposed checks. Model-free package inspection and corrected provenance preparation do not require a model call.

**6. Cheapest next checks**

In priority order; none was performed here:

1. **Mock every supervision setup failure:** constructor failure and each `Thread.start()` failure, including an already-exited root with a live descendant. Assert child/job cleanup and the intended categorized error.
2. **Inject persistence failures at recovery boundaries:** failed completed-phase write, failed pending conversion, and crashes before/after phase persistence. Assert that no subsequent inference starts without explicit disposition.
3. **Exercise retained-marker recovery:** adoption, reset and reconciliation with persistence-only uncertainty and combined process/persistence uncertainty.
4. **Test hard-owner death with an independently surviving service-like process.** Assert operator reconciliation is required.
5. **Test telemetry fallback with repository equal to home/default state.** Assert no repository file is created after rejection.
6. **Add forged-wrapper negatives:** unreachable calls, expected patch unused, changed actual argument, constructed rejection output and shadowed call/result bindings.
7. **Add infrastructure completeness fixtures:** signatures in the middle of oversized lines, nested `content`, oversized prose and reader failures.
8. **Add maintenance and capability transitions:** stale flag followed by maintenance entry, unlink/probe errors, and an allowed server enabled only at the last recheck.
9. **Test host executable selection, HEAD/read failures and exact identifier formats.**
10. **Test evidence acceptance:** tree change during testing, nonzero recorded exit, skipped tests and retained raw logs.
11. **Human-authorized model-free checks:** real launcher suspension/ownership, `findstr` behavior across representative files, and per-hunk engine controls with attributable denials.
12. **Human-authorized allowance-consuming checks:** runtime capability inventory and failed-command JSON capture; corrected fresh/resumed attacks; then the complete service/stdio path. Bind each to corrected source and configuration.

**7. Missing material**

Needed to settle the remaining source, regression, and evidence questions:

- **Full current and baseline `tests/test_remediation.py`.** The earlier review contains descriptions, not the baseline test source.
- **The production remediation diff**, to distinguish newly introduced behavior from retained defects; the stat file cannot establish that attribution.
- **Changed but unembedded helper sources:** `tools/make_telemetry_examples.py`, `tools/make_remediation_diff.py`, and `tools/make_v1_diff.py`, plus the generated telemetry examples. Their claimed rewrites/exclusion behavior cannot be reviewed from hashes.
- **`tools/live_smoke_test.py`**, to assess whether §10’s proposed smoke test actually measures runtime inventory, continuity and telemetry.
- **The two referenced v1 fixture JSON files**, to assess schema/argv equivalence assertions.
- **Raw test output and before/after source provenance**, beyond the summarized result record.
- **Verification manifest**, for the claimed exact prompt/file hash binding.
- **v1 manifests/comparison output and installation-lock verification material**, if those integrity claims are to be verified rather than recorded as reports.
- **Underlying CLI help/schema and rollout evidence** for no deny-all, web mapping/readout limits, nested-call persistence, and supported tool/event shapes.
- **Current-source real-CLI enforcement/service evidence.** It is explicitly absent and would require separately human-authorized future checks.
