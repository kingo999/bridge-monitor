# Parent Acknowledgement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record a parent-reported GPT disposition of each delivered Claude result and show accurate, separate GPT-stage metrics without claiming an observed final Codex turn.

**Architecture:** A parent-only MCP operation validates an owned Claude delivery and persists an immutable acknowledgement, exact submitted output bytes, and lineage in existing PostgreSQL tables. Stage-specific queries use Qwen deliveries for Claude and Claude deliveries for GPT; the dashboard labels parent reports and retains unknowns. The existing model route and worker completion path stay intact.

**Tech Stack:** Python, Pydantic, Psycopg 3, PostgreSQL, pytest; Next.js, TypeScript, Playwright.

**Spec:** [Parent acknowledgement design](../specs/2026-10-18-parent-acknowledgement-design.md). Also read the [Phase 1 specification](../../specifications/phase-1-specification.md), especially sections 7 and 11.

## Global Constraints

- All edits and generated artifacts remain under `C:\AI\bridge-monitor`; leave existing bridge sources, active model policy, routing and client registrations unchanged.
- The parent report is a project-local MCP caller claim, never proof of the final user-visible Codex turn or its GPT model identity. Missing/invalid reports stay unknown.
- Reuse `content_objects`, `results`, `dispositions`, `result_links`, and `runtime_disposition_obligations`; no new persistence subsystem or migration is needed.
- A delivered Claude result creates one GPT obligation; an undelivered result creates none. Sibling Claude results each need their own judgment.
- Keep stage-specific denominators, historical as-of semantics, exact UTF-8 bytes, supersession and provenance; no model run is triggered to repair accounting.
- Run database tests only against `BRIDGE_MONITOR_TEST_DSN` containing `bridge_monitor_test`, never the operational DSN. Use a fresh isolated parent MCP process for live checks before any desktop registration change.

**Suggested execution model: Claude Opus.** The parent operation changes an ownership boundary and result lineage across concurrent workers; this is advisory, and the user's chosen model remains valid.

## File map and interfaces

| File | Responsibility |
| --- | --- |
| `contracts/outcomes.py` | Strict `ParentAcknowledgement` and receipt types; no caller-supplied actor/model authority |
| `bridge/runtime/completions.py` | Create the GPT obligation when a Claude completion is recorded delivered |
| `bridge/runtime/parent_acknowledgements.py` (new) | Transactional parent report validation, exact body storage, supersession, and links |
| `bridge/mcp_server.py` | Parent-only `acknowledge_claude_result` operation, current-process ownership, and publication event |
| `service/queries/metrics.py` | Distinct Claude/GPT result sets and definition-version bump |
| `service/queries/history.py` | Effective GPT disposition and parent output detail without embedding body bytes in lists |
| `dashboard/components/MetricStrip.tsx`, `ExchangeInspector.tsx` | Provenance label, unknown state, and submitted answer inspection |

`ParentAcknowledgement` fields: `acknowledgement_id`, `result_id`, `input_sha256`, `judgment`, `reason`, `answer_text`, `supersedes_id`. It forbids extra fields; `judgment` is one of `accepted`, `revised`, `replaced`, `unused`; `answer_text` is required for the first three and optional for `unused`. The service accepts `acknowledge_claude_result(call_id: str, report: dict) -> dict` only on the parent MCP audience. Its runtime implementation is `record_parent_acknowledgement(dsn: str, *, call_id: str, producer_id: str, report: ParentAcknowledgement) -> ParentAcknowledgementReceipt`; the receipt contains `acknowledgement_id`, `output_result_id | None`, `judgment`, and `created: bool`. The coordinator first checks `call_id` in `owned_calls`, while runtime independently checks database ancestry, delivery, obligation, and hash. Derive `actor_id="gpt-parent:" + parent_call_id` from the synthetic GPT parent call; never take it from `report`. Store the parent emitter's `producer_id` as provenance, not as model identity.

## Review Focus

1. Two sibling Claude results under one GPT task must have independent obligations and judgments; identical submitted answer bytes may reuse one parent output result. Task 1 tests both.
2. Reusing an acknowledgement ID with changed content or judgment must fail atomically, rather than silently succeed through `ON CONFLICT DO NOTHING`. Task 1 tests it.
3. An unrelated parent process, Qwen result, or late/undelivered Claude result cannot create a GPT disposition. Tasks 1–2 test each boundary.
4. A parent answer submitted before the final visible turn must be labeled parent-reported; no model or user-delivery time is inferred. Tasks 1 and 3 test its stored provenance and UI.
5. Retained old Claude results with no new obligation still enter the GPT-stage denominator as unknown; synthetic Qwen-only fixtures do not. Task 3 tests both as-of cases.

---

### Task 1: Persist exact parent reports and delivery obligations

**Files:** Modify `contracts/outcomes.py`, `bridge/runtime/completions.py`; create `bridge/runtime/parent_acknowledgements.py`, `tests/integration/test_parent_acknowledgements.py`; modify `tests/integration/test_dispositions.py`.

**Interfaces:** Produce the `ParentAcknowledgement` and `ParentAcknowledgementReceipt` types and `record_parent_acknowledgement(...)` signature above for Task 2. Use the existing result/disposition/lineage tables and advisory transaction lock. No new migration.

- [ ] **Step 1: Write failing database tests.** Assert one GPT obligation per delivered Claude result, none for undelivered results, and independent obligations for siblings. Assert accepted/revised/replaced/unused storage, exact UTF-8 bytes/hash, parent output result with no claimed model execution or user delivery, shared output for equal parent text, no link for unused, idempotent identical replay, explicit supersession, and rollback on changed replay ID, unrelated result, Qwen result, bad hash, missing obligation, or cycle.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/integration/test_parent_acknowledgements.py tests/integration/test_dispositions.py -q` with the dedicated test DSN; expect the new cases to fail for missing operation/obligation while existing tests retain their baseline.
- [ ] **Step 3: Implement the contract and transaction.** Call `_create_obligation(..., stage="gpt")` from `record_completion` only for a delivered Claude result with a synthetic GPT parent. Validate the report strictly; derive the actor from the stored parent call, verify current result/obligation/hash and existing effective record, store the submitted body and parent output result deterministically by parent call plus body hash, then commit disposition and relation atomically. Use `dispositions.details.provenance="parent_mcp_report"` and `content_objects.capture_layer="parent_mcp_submission"`; do not mark submitted output as user-delivered.
- [ ] **Step 4: Run green tests.** Repeat the focused command; expect all selected tests to pass and no inference invocation.
- [ ] **Step 5: Commit only Task 1 files.** Message: `bridge-monitor: persist parent acknowledgements`.

### Task 2: Expose the parent-only operation and live publication

**Files:** Modify `bridge/mcp_server.py`; create `tests/adapters/test_parent_acknowledgement_mcp.py`.

**Interfaces:** Consume Task 1's `record_parent_acknowledgement(...)`. Produce `acknowledge_claude_result(call_id: str, report: dict) -> dict` on the parent MCP audience; return report ID, output result ID if present, and persisted judgment. A successful new report publishes `disposition.recorded` with server-derived actor/stage and existing work-item/call correlation; an identical replay publishes no second logical report.

- [ ] **Step 1: Write failing MCP tests.** One owned delivered call accepts a report and replays it idempotently; another parent coordinator, a restarted coordinator with no owned call, and the Claude-audience MCP reject it. The event spool publishes the accepted disposition, while an invalid report creates neither row nor publication.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/adapters/test_parent_acknowledgement_mcp.py -q` with the dedicated test DSN; expect missing-tool/ownership failures.
- [ ] **Step 3: Add the parent endpoint.** Parse `ParentAcknowledgement`, check `owned_calls`, call the Task 1 runtime transaction, and emit the validated event through the existing durable emitter. Do not route through `complete_work` or accept an actor/model field from the MCP payload.
- [ ] **Step 4: Run green tests.** Repeat the focused command; expect all selected tests to pass, including no duplicate acknowledgement row after replay.
- [ ] **Step 5: Commit only Task 2 files.** Message: `bridge-monitor: expose parent acknowledgement`.

### Task 3: Correct stage metrics and show reported evidence

**Files:** Modify `service/queries/metrics.py`, `service/queries/history.py`, `dashboard/components/MetricStrip.tsx`, `dashboard/components/ExchangeInspector.tsx`; add cases to `tests/integration/test_metrics.py`, `tests/integration/test_history.py`, `dashboard/tests/metrics-history.spec.ts`.

**Interfaces:** `query_metrics(dsn, CohortQuery(consumer_stage="gpt"))` counts delivered Claude result IDs from root/recovery paid calls as R; `consumer_stage="claude"` retains delivered Qwen IDs. The work detail exposes the effective GPT disposition and the parent output's existing content reference for authorized body loading. Keep the existing API shapes unless a tested addition is necessary.

- [ ] **Step 1: Write failing query/browser tests.** Seed one Qwen result and two delivered Claude sibling results, acknowledge one, and assert Claude R=1 versus GPT R=2, GPT K=1, GPT unknown=1, plus correct accepted numerator. Cover old delivered Claude results without obligations, as-of before/after a late report, a superseded record, and a Qwen-only synthetic fixture. In Playwright assert the GPT label says parent-reported, the submitted answer is inspectable with a capture warning, and an unacknowledged row remains Unknown.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/integration/test_metrics.py tests/integration/test_history.py -q`; from `dashboard`, run `npm run test:e2e -- metrics-history.spec.ts`. Expect the new stage/provenance cases to fail.
- [ ] **Step 3: Implement the stage query and display.** Select GPT result units by paid Claude delivery to the synthetic GPT parent, including existing records without obligations; retain Qwen dispatch selection for Claude stage. Use only valid effective dispositions and bump the report definition from `phase1-v1` to `phase1-v2`. Show the parent-submitted answer via the existing authenticated body API; do not embed bytes in work-list metadata or imply final-user delivery.
- [ ] **Step 4: Run green checks.** Repeat both focused commands, then run the full Python suite, `npm run typecheck`, `npm run build`, and `npm run test:e2e` in `dashboard`; expect no failures outside documented environment skips.
- [ ] **Step 5: Commit only Task 3 files.** Message: `bridge-monitor: report GPT-stage coverage and parent claims`.

### Task 4: Controlled native proof and exit evidence

**Files:** Create `docs/verification/parent-acknowledgement-2026-10-18.md`; add only project-local test harness or receipt files needed for the isolated check.

**Interfaces:** Use Task 2's parent MCP operation and Task 3's query/inspector. Do not replace active routing policy or ask a model to rerun merely for an acknowledgement.

- [ ] **Step 1: Prepare an isolated parent process against the project database.** Record active policy hash, current project Git state, capture health, and the exact Claude result to acknowledge. Use a new controlled call if there is no suitable owned call; never claim ownership of a prior desktop process's call.
- [ ] **Step 2: Submit one parent report and inspect persisted evidence.** Verify the submitted text/hash, actor, GPT disposition, link, metric R/K/A, event publication and dashboard provenance. A model-reported judgment remains a report, not a quality verdict.
- [ ] **Step 3: Run exit checks and write the receipt.** Confirm full suite/build/browser results, no held capacity added by acknowledgement, unchanged active routing/model pins, no existing-bridge tracked/staged diff, and honest unknowns for unacknowledged calls. Record any unavailable live-client capability rather than fabricating a complete chain.
- [ ] **Step 4: Commit only the evidence and necessary project-local harness files.** Message: `bridge-monitor: verify parent acknowledgement pilot`.

The implementation method already chosen for this project is native execution in the current chat with reviewable checks. Review this plan before implementation; that choice does not switch models or authorize changing the existing bridge.
