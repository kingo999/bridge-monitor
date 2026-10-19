# Gemma route work capture implementation plan

> For agentic workers: execute the six tasks below in order, with tests before implementation and independent verification before each completion claim.

**Goal:** Implement the reviewed default-off registered-case observer without changing routing authority or deploying it to the live parent.

**Architecture:** A parent-only observer freezes bounded inputs and offers them to one background persistence thread. Classifier-only accounting bounds durable capture; exact submission bindings and a saved indexed cursor support later route enrichment.

**Tech stack:** Python 3.13, Pydantic, Psycopg 3, PostgreSQL, pytest, existing MCP and dashboard.

**Spec:** [Reviewed capture hook draft](../../design/gemma-route-work-capture-hook-2026-10-19.md).

**Approval:** The user approved execution in this chat on 19 October 2026: “ok, then execute the plan in auto mode here and claude”. This approves implementation and isolated verification, not live deployment.

**Suggested execution model: Claude Opus.** Advisory because concurrency and evidence binding cross the routing trust boundary. The current Claude Code default remains unchanged.

## Global constraints

- Base revision: `c89b380a268065de6a24abbe22f8a8537220f9b3`. The two runner fixes are already complete and must remain protected.
- Work only in this isolated checkout's `bridge-monitor` directory. Preserve the original checkout, unrelated IDE/project changes and the reviewed draft.
- Do not apply migrations to the operational database, change live policy, restart services, start Gemma/Skynet models, make paid test calls, push or merge.
- Run database tests only against verified `bridge_monitor_test` databases. Model and paid-worker tests use fakes.
- Both enable switches default false. The observer's return value never enters routing or MCP responses.
- Eight pending envelopes, one active worker, 24,576 combined request/source characters, identifiers at most 256 characters, provenance at most 4 KiB, input age at most 600 seconds.
- Durable hook caps: 256 observations, 4 MiB serialized input, one successful persistence per two seconds, with no reset on restart or profile changes.
- Hook database bounds: request connect_timeout=1, lock 50 ms, statement 250 ms. Installed Psycopg enforces a two-second connect minimum per address; this driver limit is recorded in operations rather than hidden behind private driver overrides. Shutdown joins at most 250 ms; the worker is a daemon.
- Keep manual capture behavior and its existing defaults. Keep migration 007 unchanged; propose migration 008 only for classifier accounting and the pending-link index.

## Execution arrangement

The Claude MCP implementation bridge is configured for an unrelated MRI checkout and cannot select a per-job working directory. Do not dispatch edit jobs there or change its configuration. Codex implements in this checkout; Claude's enforced `review_changes` mode reviews this exact Git root through `additional_read_roots`. Compare source and Git state before and after that review. No Skynet work is necessary.

## Review focus

- Optional worker, diagnostics or locks must never delay authoritative dispatch; exercise a blocked persistence operation and contention.
- Conflicting first submissions sharing a work item must retain separate identities and never borrow each other's decision.
- Ambiguous commits and process restarts must not bypass durable accounting or cause automatic replay.
- Missing new schema must disable optional paths while keeping existing manual classification and parent startup usable.
- A long unresolved history and single-page manual invocations must still make saved progress toward later resolvable rows.

## Task 1 — Frozen configuration and inputs

- [x] Write failing configuration and binding tests.
- [x] Add `contracts/route_capture.py` for a frozen primitive-only snapshot and observer protocol; no classifier imports in this contract.
- [x] Add `bridge/classifier/hook_config.py` for project-contained explicit TOML selection, both enable switches, immutable private configuration, pinned identity and explicit fact assertions.
- [x] Add `bridge/classifier/hook_capture.py` to validate the bounded snapshot, freeze trusted facts, generate a canonical submission binding and construct `ObservationCapture` without database/model access.
- [x] Verify configuration defaults, mutation isolation, case/task/contract/scope checks, pre-solution timing, excluded hidden data and distinct case bindings.

## Task 2 — Atomic bounded persistence

- [x] Write failing database tests for hook drops, quota/rate boundaries, deduplication, restart/concurrent accounting and rollback.
- [x] Add `008_classifier_route_capture.sql`: fixed-scope budget row, versioned route-link progress row and partial pending-link index; no production table changes.
- [x] Factor the existing observation insert into a shared transaction-local helper without changing manual semantics.
- [x] Add `bridge/classifier/hook_store.py`: dedicated bounded connections, metadata-only earlier-result check, queue and budget checks under the capture lock, content/observation/accounting in one transaction.
- [x] Keep drops out of durable observations/content; disable a sink on database/ambiguous commit failures. Adapt test fixtures so accounting is isolated without changing production restart behavior.
- [x] Preserve migration-007-only test isolation and verify migration 008 in dedicated databases only.

## Task 3 — Background observer and lifecycle

- [x] Write failing barrier, overflow, contention, expiry, mutation and shutdown tests.
- [x] Add `bridge/classifier/observer.py` with one daemon worker, bounded deque, nonblocking submission mutex and no request-thread wakeup/log/database calls.
- [x] Freeze private configuration at construction; apply cooldown, validate in the worker and call Task 2 persistence.
- [x] Latch database failures/budget exhaustion, discard pending envelopes, use bounded rate-limited stderr diagnostics off the request thread and expose fixed-name counters.
- [x] Verify no extra threads per request and that a blocked worker cannot hold parent return or shutdown indefinitely.

## Task 4 — Exact linking and saved reconciliation

- [x] Write failing identity-mismatch, delayed publication, all-unresolved sweep, restart/page, wraparound and rollback tests.
- [x] Add `bridge/classifier/route_links.py`: exact expected decision/call/task/work-item/hash/case/contract matching shared by immediate and late hook links.
- [x] Batch at most 64 indexed candidates per pass, advancing by captured timestamp/ID with a fixed sweep upper key. Save progress past unresolved candidates; update route/provenance/time and cursor transactionally.
- [x] Extend the runner's existing isolated lookup for hook bindings, retaining manual lookup semantics and the committed NULL/error/no-resend fixes.
- [x] Run optional reconciliation before endpoint checks; missing schema/errors never prevent an otherwise healthy classifier outcome write. Repeated timeouts suspend enrichment for the invocation.

## Task 5 — Parent integration

- [x] Write failing paired routing/startup/audience tests using existing paid/local/verifier doubles.
- [x] Inject an optional observer in the parent bootstrap and coordinator. Offer once after the existing fresh-branch case load, before facts/decision/paid execution; keep the recovery branch untouched.
- [x] Add bounded cleanup around MCP lifetime. Invalid configuration/import/worker setup never prevents normal parent startup. Child/shadow audiences receive no observer.
- [x] Preserve schemas, response/request bytes, admission/reservations/jobs/results/fallback and existing structural protections. Do not add a classifier-to-dispatch dependency.

## Task 6 — Reporting, operations and verification

- [x] Add failing cohort tests distinguishing hook/manual captures, unique work items and unknown-code-capacity blocks from known adverse facts.
- [x] Extend only classifier reporting and its panel; preserve production metrics, Qwen cohort and operator-only content access.
- [x] Add a default-off example section and operations instructions for approvals, accounting, lost coverage, rollout/rollback and stalled reconciliation.
- [x] Run the full authorized Python test suite and dashboard type/build checks using existing dependencies; record named failures/skips honestly.
- [x] Request a source-grounded enforced read-only Claude review with this checkout as an explicit read root. Verify source/Git state before and after; fix substantive findings with regression tests.
- [x] Confirm live policy, operational counts, model/runtime identities and stopped Gemma state are unchanged. Leave deployment disabled and provide the branch, commits and verification evidence.

Execution completed in the isolated checkout. See [verification record](../../reviews/gemma-route-work-capture-verification-2026-10-19.md). Deployment, migration 008 on the operational database, restarts, live enablement, merge and push remain outside this run.
