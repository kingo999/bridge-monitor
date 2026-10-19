# Gemma route_work capture verification — 19 October 2026

Implemented on `codex/gemma-route-capture`, based on `c89b380a268065de6a24abbe22f8a8537220f9b3`.
The implementation is disabled and has not been deployed. The original design draft and
migration 007 remain unchanged.

## Result

- Parent-only arrival capture requires both explicit enable switches. Existing manual-only
  configuration keeps it off. Child/shadow audiences never create a sink.
- One immutable, bounded snapshot is offered on the fresh registered-case path. Recovery
  retries do not offer again. No tests, expected outputs or answers enter the snapshot.
- A single daemon worker owns validation and persistence. Non-waiting offers, eight pending
  items, cooldown, expiry and bounded shutdown isolate production requests from the sink.
- Migration 008 proposes classifier-only lifetime accounting and saved reconciliation
  progress. Content, observation and budget charge are atomic; drops persist no payload.
- Immediate and late links share exact submission validation. Indexed pages examine at most
  64 candidates, advance past unresolved/malformed decisions and wrap across invocations.
- Reporting separates capture methods, overlapping work items, fact coverage and unknown
  code-capacity blocks. Production metrics, Qwen reporting and content authorization are preserved.

## Verification

| Check | Result |
| --- | --- |
| Full Python suite on final product code | **580 passed, 2 skipped**, 342.46 seconds |
| Browser regression, installed Chrome | **4 passed**: all/manual/hook filters, router panel compatibility and operator session |
| Dashboard TypeScript | `npm run typecheck` passed |
| Production static dashboard build | `npm run build -- --webpack` passed |
| Git whitespace checks | `git diff --check` passed |
| MCP boundary | AST of `create_mcp` unchanged; `route_work` AST unchanged after removing its single new offer expression |
| Routing pair | Off/on fixtures preserve original request bytes, semantic response/decision fields, jobs and reservation deltas; generated identities are excluded from response comparison |
| Representative backlog | 10,000 incomplete rows; PostgreSQL used `classifier_pending_hook_link_idx`, and a pass examined 64 rows |
| Hot-path sample | Isolated offer helper: disabled median 0.1 microseconds; eight accepted samples median 9.25 microseconds; full-queue median 8.0 microseconds over 10,000 offers |

The timing sample used fake, blocked persistence under concurrent tests. Eight accepted
samples do not establish a tail-latency guarantee. The default Turbopack build could not
traverse the shared dependency junction outside its root; the supported webpack build passed
without a package/configuration change or dependency download. Browser verification used
installed Chrome because this Playwright version's bundled Chromium was absent.

The two existing skips are:

- `tests/concurrency/test_workspaces.py::test_symlink_out_of_workspace_is_rejected`: host
  cannot create symlinks.
- `tests/pilot/test_single_flow.py::test_codex_sonnet_qwen_normal_flow`: live-model opt-in
  was deliberately not supplied.

One existing FastAPI/Starlette TestClient deprecation warning remains. Model and paid-worker
tests used fakes; no production paid dispatch or Gemma/Qwen inference was used for testing.

New regression coverage includes exact binding mismatches, no-resend/response preservation,
late linking with Gemma unavailable, unresolved-page progress and wraparound, transaction
rollback, missing schema, durable quota/rate boundaries, concurrent final-slot accounting,
lost commit acknowledgement without a second charge, earlier delivered-result metadata,
malformed Unicode, broken diagnostics, per-row malformed decision JSON, original timestamps,
oversize/full-queue drops, shutdown contention and the hard 600-second hook expiry.

An integration run exposed a test-isolation error: the malformed decision fixture remained
in the dedicated database and broke a later reporting test. That test now restores its
original facts in `finally`. Production router reporting was not changed. The final full
run includes that cleanup and passes.

Local execution receipts/logs are in the ignored `var/hook-execution/` directory, including
`final-all.xml`, `final-all.log`, `browser-final.log`, build output, AST evidence, review
snapshots and the latency sample. These are local evidence, not deployed files.

## Independent Claude review

Three enforced read-only jobs used this exact worktree as an additional read root:

1. `15077729-e171-40d9-9f14-7cbb09a205c9`: broad architecture/failure review.
2. `de8361dc-5bbd-47df-9f51-9f252e38f14a`: actual diff artifact, tests, UI and operations follow-up.
3. `9d73238b-0900-42e9-9ee2-35ed4fb6f916`: focused review of the final metadata/error and rollback corrections;
   **no new actionable correctness or safety issues**.

The reviews had no edit, shell, test or external-tool capability. Their own before/after
source and Git verification reported unchanged target and unrelated primary MRI roots.
Independent snapshots also showed unchanged relevant source and heads. The original
`C:\AI` Git status gained only the bridge's normal review receipts under
`gpt-claude-mcp-bridge-v2/tools/claude-bridge/.receipts/`; those permitted receipts are
preserved outside this implementation commit.

Resolved findings include consecutive timeout suspension, known rollback versus uncertain
commit reporting, per-envelope validation isolation, the final provenance cap, malformed-row
cursor fairness, older-loader rollback compatibility and quiet inherited external manual
configuration. All database errors still disable the sink; contention was not turned into
an automatic retry. Duplicate containment checks remain intentional because explicit loader
calls reject external files while parent bootstrap quietly ignores inherited manual paths.

Reviewers did not execute tests, inspect live state or establish OS timing guarantees.
Their initial coverage limits were addressed with a diff artifact, targeted follow-up and
the independent checks above. Passing tests and source reviews do not authorize rollout.

## Live boundary and remaining rollout work

Read-only checkpoints before and after execution match on every field except their label
and capture timestamp: policy, database counts, migration level, runtime/source identities
and original Bridge Monitor Git state are unchanged. The active policy hash remains
`43a200ea7bf9f73aad0cd5f642e5b51230ffd405f275342a81bc911b906770b2`.
The operational database remains at migration **007**. Gemma remains stopped, with no owned
process receipt and no listener on port 11435.

No live migration, policy change, service restart, model start, schedule, MCP configuration
change, merge or push was performed. Implementation and test fixtures stayed in the isolated
worktree; database writes used only `bridge_monitor_test` and disposable classifier test databases.

Connections request `connect_timeout=1`, but the installed Psycopg 3.3.6 implementation
enforces a **two-second minimum per address**. This verified driver floor is documented in
the implementation plan and operations guide; no private driver override was introduced.
The current numeric loopback DSN has one address. These settings do not bound every OS stall.
The 256-row/4 MiB budget measures logical input storage, not physical index/WAL/backup size.

Live index locking, physical database/WAL growth and rollout measurements remain separate
operator-approved deployment work. See the operations guide for backup, migration 008,
disabled deployment, controlled-case enablement and rollback. Startup configuration is frozen;
editing a file alone does not stop an already-running observer. Preserve all captured evidence,
budget and progress when disabling or rolling back.
