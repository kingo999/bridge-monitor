# Isolated Operation Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline with test-driven-development. Direct Codex execution is explicitly selected.

**Goal:** Persist and project stub candidate operation observations through the existing telemetry flow in an isolated lab.
**Architecture:** Explicit opt-ins extend emitter/spool/store. An additive candidate SQL artifact and bounded query preserve original operational defaults and publication semantics.
**Tech Stack:** Existing Python, Pydantic, pytest, SQLite, psycopg and PostgreSQL 18. No installation.
**Spec:** docs/superpowers/specs/2026-10-22-operation-lifecycle-isolated-design.md

## Global Constraints
- Branch codex/skynet-task-status; direct Codex; no activation/deployment.
- Keep operational launcher/config/policy, automatic migrations and clients unchanged.
- Disposable PostgreSQL and scratch-only validation; no external execution.
- Candidate schema/source provenance remains unchanged.
- Suggested execution model: Claude Sonnet, advisory because existing telemetry patterns and focused tests define the work; preserve chosen model.

## Review Focus
- Disabled emitter with candidate-enabled spool must retain candidate head without delivery.
- Lost receipt after real commit must replay as duplicate, with no operation re-execution.
- Different IDs sharing producer sequence must roll back completely.
- Backdated/conflicting outcomes and incomplete capture must never fabricate elapsed time.
- Candidate insertion must not contaminate legacy publication/status/client contracts.

## Task 1: Explicit capture and replay
Files: modify bridge/telemetry/spool.py and emitter.py; create tests/adapters/test_operation_capture.py.
Interfaces: DurableSpool(..., allow_operations: bool=False); TelemetryEmitter(..., allow_operations: bool=False); emit_operation(event_type, *, operation_id, operation_kind, task_id, work_item_id, payload, origin, capture_completeness, **correlation) -> bool.
- [ ] Write tests for strict opt-in/default refusal, valid owned envelope/sequence, malformed identity override rejection, seven variants, restart mixed replay, disabled delivery and overflow.
- [ ] Run and observe missing API/flag failures.
- [ ] Implement flags and candidate validation via reviewed reader; keep historical defaults.
- [ ] Run capture tests plus existing capture/reader contract tests.
- [ ] Commit capture/replay changes only.

## Task 2: Candidate persistence and projection
Files: modify service/ingest/store.py; create service/ingest/operations.py, service/queries/operations.py, database/candidates/operation_lifecycle_v2.sql and tests/integration/test_operation_lifecycle.py.
Interfaces: ingest_batch(..., allow_operations: bool=False); prepare_operation_events(events, allow_operations) -> normalized models; lock_operations(conn, events); persist_operation(conn,event) -> committed|duplicate; operation_snapshot(conn, operation_id, *, allow_operations=False, max_events=1000) -> dict|None.
- [ ] Write tests before implementation; disabled API rejects before connection; integration fixture requires explicitly named isolated test DSN and applies candidate SQL only there.
- [ ] Observe missing-module/API failures.
- [ ] Implement additive facts against existing event/sequence tables, immutable operation identity, sorted transaction locks and bounded read projection. Keep candidate events outside legacy publications/status.
- [ ] Freeze source and run candidate + selected historical tests in owned PostgreSQL; verify migration idempotence, real persistence, commit-loss recovery and conflict rollbacks. Keep the same frozen bytes for a green retry unless product changes require a new snapshot.
- [ ] Commit persistence/projection and tests after green evidence.

## Task 3: Documentation, review and handoff
Files: docs/specifications/operation-lifecycle-isolated.md plus this design/plan.
- [ ] Document explicit APIs, projection rules, real consumer support, future gates and dependency-aware rollback.
- [ ] One enforced read-only fresh whole-change review; verify source and Git before/after. Fix confirmed important findings with red/green tests; ledger deferred minors and rulings.
- [ ] Verify source preservation, only scoped diff, clean product/index, candidate provenance and lab teardown. Retain evidence in scratch.
- [ ] Commit scoped documentation/fixes. Record direct task completion through existing metadata API. No push/merge/activation/deployment.

## Execution rulings
The user's explicit instruction authorizes implementation of the proposed next slice and selects execution method. Do not request repeated approval for spec/plan artifacts generated to make that approved work concrete. Use the existing requested branch, not a new worktree. Scratch evidence is retained, not deleted.
