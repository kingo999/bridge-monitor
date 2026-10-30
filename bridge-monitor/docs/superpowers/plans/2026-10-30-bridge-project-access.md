# Bridge project access and worker capacity implementation plan

Goal: upgrade the existing Bridge only; keep its router, audit, cancellation and
model policy. MRI3 is the user-confirmed D:/IdeaProjects/mine/mri3, registered
as `mri3`; the earlier services root `mri` remains separate. Do not edit or execute MRI3.
Stage the resulting Bridge changes without committing.

Suggested execution model: Claude Opus (advisory), because repository grants and
termination evidence cross consequential trust boundaries. Respect the chosen
parent model and existing Bridge entry profile `sonnet`.

Architecture: extend the current leased repository MCP rather than introduce a
router or general shell. Task grants must fail closed to read-only and include
explicit selected roots. Builds are mutating operations. Keep process jobs,
fencing, source ownership, expected-content hashes and operation receipts.

User steering: fix worker_limit first. Existing uncommitted work is preserved in
place; baseline patches and source copies are under
`var/authorization/bridge-access-20261030`. The Git root is C:/AI, so staging must
name Bridge files explicitly. No commits or worktree checkout changes.

## Task 1: worker capacity

- [x] Reproduce against active metadata, without reading prompt bodies.
- [x] Write failing DB tests in tests/integration/test_worker_capacity.py for
  expired never-launched queues, cancellation, idempotency and uncertain owners.
- [x] In bridge/runtime/codex_claude.py, reconcile expired queued reservations
  under the existing arbitration lock; cancel/debit once, release only proven
  never-launched holds, and append the existing release audit. Keep unknown costs.
- [x] In bridge/mcp_server.py, reconcile confirmed bounded analysis exits as well
  as repository exits. Never automatically replay a failed bounded call.
- [x] Run focused tests on a dedicated test DB, then safely reconcile production
  expired queues. Preserve the uncertain call without correlated exit evidence.
- [x] Verify capacity using the pending useful design task, not a paid probe.

## Task 2: project grant boundaries

- [x] Add tests for explicit roots, task read-only mode, no read-only builds,
  narrowed write grants and policy binding changes.
- [x] Update existing RepositoryPolicy, tools and worker registrations; reject
  invalid grants before launch, include permissions in the existing binding.
- [x] Keep MRI3 allowlisting and earlier independently authorized roots intact.

## Task 3: verify and document

- [x] Run focused boundary, ownership, cancellation and routing tests, followed
  by the ordinary suite; disclose failures and skips precisely.
- [x] Document permission migration, worker-limit findings, activation and risks.
- [x] Check MRI3 and existing edits against baseline; stage only Bridge changes.

Review focus: a queued call racing dispatch; cancelled calls that actually ran;
unknown usage versus process capacity; omitted access permissions; build/plugin
side effects. No limit increases, invented exit proof, guessed usage or charges.

## Execution and review ledger

Task 1: RED 2 failed/5 passed, then queue reconciliation GREEN 7/7. Monitor and
bounded-exit regressions RED, then GREEN 12/12. Live monitor released exactly
three abandoned queues and preserved the uncertain execution. Expanded capacity,
cancellation and authentication run: 23 passed. The worker limit remained four.

Task 2: grant tests RED 22 failed/2 passed, then GREEN 24/24. Expanded boundary,
worker/stdio and completion run: 110 passed, 2 skipped. Confirmed MRI3 root
read-only grant validation rejected writes and checks before mutation.

Read-only Bridge review: call 5923b092-b35b-5971-8ca9-16032dda2a4b, requested
entry sonnet, selected/reported claude-opus-5-5. Source hashes and Git status
unchanged during review. No reviewer edits were permitted or performed.

Final: fixed multi-root check/campaign grants and read-only Git fsmonitor execution
via real RED-to-GREEN regression tests. Fixed failed reconciliation handling and
bounded database authority lookups with RED-to-GREEN DB regressions. Review-fix
run: 94 passed, 1 skipped. Final focused run: 151 passed, 2 skipped; one upstream
Starlette/httpx deprecation warning. MRI3 and the earlier services root Git
status, working diff and staged diff match the saved baselines.

Final: Ruling: arbitrary hostile base CLI flags are not accepted inputs to the
repository command builder. The actual base already forces restricted mode,
strict MCP config, empty built-in tools, dontAsk, disabled hooks/slash commands
and explicit built-in denials; both CLI tests and frame checks enforce that
boundary. Do not add a second command/provider-selection layer. Cost if wrong:
tool execution outside a grant; retain these command-boundary tests.

Final: Ruling: a dispatch intention is not a provider launch. Under the shared
arbiter, queued cancellation fences claim_dispatch and claim_launch; verified
prelaunch abort may retain a lease but cannot launch after cancellation. Keep
safe queue release for these states. Tests cover both cases. Cost if wrong:
under-counted capacity/no-charge evidence; never weaken the state/launch guards.

Final: Ruling: cleanup/audit/budget-integrity failure deliberately blocks admission
and rolls back rather than continuing with inconsistent accounting. Monitor
health reports errors and retries. Cost: availability during a genuine database
integrity fault; preserve holds and repair with evidence, not silent counter resets.

Final: Ruling: repository source locks are released by existing termination
reconciliation and successful completion; uncertain ownership remains locked.
Read-only locks remain exclusive as a conservative existing choice. Cost:
read-only reviews serialize per project; no unsafe concurrent edit is admitted.

Final: minor (deferred): secret-name denylist remains incomplete; it cannot detect
credentials embedded under arbitrary source filenames.
Final: minor (deferred): case-insensitive .mvn write-denial edge remains under
host prefixes that grant .mvn; no new MRI3 host write prefix grants that directory.
Final: minor (deferred): large-file reads still allocate bytes before budget check.
Final: minor (deferred): Git status exposes filenames; source access permissions
must account for that, and the endpoint is not a secret-name redactor.

No commits. Existing work is preserved; relevant upgrade files are staged only
after verification. The broad suite's 18 failures and four skips are disclosed;
the three temporary-path shadow failures passed on rerun inside Bridge.
