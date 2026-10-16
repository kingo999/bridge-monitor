# SDD ledger — plan: docs/superpowers/plans/2026-10-15-local00-implementation.md
Execution: native; user approved plan and execution. V2 primary; v1 manual fallback only.
Ruling: Work in the approved plain v2 directory with .baseline_local00 and per-task checkpoints — no Git repository exists, so Git-only workspace scripts/commits do not apply — rollback relies on file snapshots rather than commits.
Ruling: Use direct model-free fingerprint() for checkpoints — the CLI fingerprint command also runs unrelated Codex doctor and is unnecessary per task — environment metadata is recorded for final suite evidence.
Pre-flight: 1 -> all: recursive fingerprint must include nested package/fixtures; fix first.
Pre-flight: 2 -> 3/4/5/6/7/8/9/10: shared strict models; signatures reconciled in plan.
Pre-flight: 3/4 -> 5/7/9: worker lease owns durable active marker; never architect registry lock.
Pre-flight: 5/6/7/8 -> 10: service owns async dispatch and session revision; factory injects offline tests.
Pre-flight: 6 -> 7: reviewed proof must bind exact runtime/template identity; unproven generation unavailable.
Pre-flight: 10 -> 11: live runner uses production shared worker lock; only unit tests isolate home.
Task 1: in progress.
Task 1: complete — provenance test RED missing nested code -> GREEN 1/1; native test runtime required because sandbox temp ACLs refuse nested directory creation. Existing baseline suite running from immutable source copy.
Task 2: in progress.
Task 2: complete — tests ['tests.test_local_worker_models'] GREEN; evidence task-02-tests.log, task-02-source.json and diff.
Task 3: complete — tests ['tests.test_local_worker_storage'] GREEN; evidence task-03-tests.log, task-03-source.json and diff.
Task 4: complete — tests ['tests.test_local_worker_transport'] GREEN; evidence task-04-tests.log, task-04-source.json and diff.
Task 5: complete — tests ['tests.test_local_worker_service'] GREEN; evidence task-05-tests.log, task-05-source.json and diff.
Task 6: complete — tests ['tests.test_local_worker_budget'] GREEN; evidence task-06-tests.log, task-06-source.json and diff.
Task 7: complete — tests ['tests.test_local_worker_generation'] GREEN; evidence task-07-tests.log, task-07-source.json and diff.
Task 8: complete — tests ['tests.test_local_worker_validation', 'tests.test_local_worker_generation'] GREEN; evidence task-08-tests.log, task-08-source.json and diff.
Task 9: complete — tests ['tests.test_local_worker_admin'] GREEN; evidence task-09-tests.log, task-09-source.json and diff.
Ruling: Baseline 482/483 passed; test_windows_sandbox_override_is_opt_in_and_validated reads real home settings.json — isolate only that test settings directory to restore its no-file premise; production sandbox behaviour/settings stay unchanged — risk if wrong: test could conceal a settings loader regression, covered separately by test_review10.
Task 10: complete — tests ['tests.test_local_worker_mcp', 'tests.test_server_admin', 'tests.test_config_argv_auth.ReadOnlyArgv.test_windows_sandbox_override_is_opt_in_and_validated'] GREEN; evidence task-10-tests.log, task-10-source.json and diff.
Task 11: in progress — focused tests pass; first-start native race reproduced RED then GREEN and live metadata/reconnect now available. Audit metadata overwrite reproduced RED then GREEN; retains generation timing alongside validation effort. Full tests/live acceptance/final fresh review pending.
Final review: fresh gpt-6-astra reviewer (native execution skill required one whole-change review); four Important, one Minor; no Critical.
Final: fixed queued reconnect/startup revision — queued-reconnect and continue-before-startup tests RED -> GREEN.
Final: fixed obsolete endpoint selection — old-host POST reproduction RED -> GREEN, plus same-host revision guard.
Final: fixed malformed witness escaping startup — corrupt-witness shapes RED AttributeError -> GREEN WorkerError; real stdio/architect guard added.
Final: fixed replayed older verdict reversing later correction — accepted -> failed -> replay accepted RED -> GREEN; current effective verdict preserved atomically.
Final: minor (deferred): completed attempt-2 replay ignores a conflicting/missing feedback_ref; returns the existing draft without another POST. Requires future request-identity refinement.
Final: Ruling: External inference clients and changes after the final compatibility check remain outside cooperating-host lock coverage — preserve approved pilot exclusion policy — external clients can violate sequential operation if that policy is ignored.
Final: Ruling: Source/operator approval references remain supervisor/operator assertions — approved design records evidence without authenticating the human conversation — false assertions can authorize inappropriate acceptance or recovery.
Final: Ruling: Real pilot clarification is unverifiable; supervisor correction is separate from worker success — no local feedback retry or new task ID to disguise a failed pilot — observed run establishes no worker success or savings.
Final: Ruling: Full-suite/release evidence deferred by reviewer is resolved by rerunning the staged suite after fixes and reporting process-restart scope explicitly — source tests alone cannot prove an already-running MCP process exposes the new tool — wrong scope would misstate activation.
Final fix pass: 31 related regression tests GREEN, real stdio corrupt-witness and hard-kill guards included. First full run contained two legacy harness assumptions (shallow fixture glob/exactly two tools); both reproduced and corrected without weakening original architect schema checks. Current final staged run uses label local00_full_final.
Ruling: Retain the plain-directory execution ledger and source baseline — there is no Git history to recover their record after scratch deletion — costs additional local evidence files, preserves rollback/review information.
Task 11: complete — final staged full suite 531/531, zero skips/problems, hashes equal 3b76e942f384c846467d9cebf62de24105c5d0ecfae782c85e4d2565f0d92b3b; real native metadata/reconnect passed; one real worker clarification unaccepted and supervisor correction independently validated. V1/config/auth/registration integrity and 30-package environment unchanged.
Final review fix pass: complete; all four Important findings RED -> GREEN; final whole suite GREEN. One Minor deferred as documented. Native snapshot/ledger retained because no Git history exists.
