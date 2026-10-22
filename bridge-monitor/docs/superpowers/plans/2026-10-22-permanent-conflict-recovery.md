# Permanent Conflict Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline, test-driven-development and verification-before-completion.

**Goal:** Recover a selected conflicting telemetry head while retaining exact evidence and safe degraded status.
**Architecture:** Typed candidate conflict evidence + explicit local recovery API + additive journal in the same SQLite spool; no automatic activation.
**Tech Stack:** Existing Python/Pydantic/SQLite/pytest/psycopg/PostgreSQL 18. No installation.
**Spec:** docs/superpowers/specs/2026-10-22-permanent-conflict-recovery-design.md

## Global Constraints
- Direct Codex on codex/skynet-task-status; no activation/deployment/push/merge.
- Preserve operational config/policy/services and existing defaults.
- Scratch-only tests; no underlying operation execution or evidence deletion.
- Suggested execution model: Claude Sonnet, advisory for an established telemetry extension with fault tests; respect chosen model.

## Review Focus
- Stale selection and recovery-ID reuse must never act on a different head.
- Typed conflict must bind to the canonical current event; text and transient failures refuse.
- A collector commit followed by lost receipt must never become quarantine.
- Journal insertion/removal must roll back together and stay within a finite bound.
- Tail can drain while quarantine keeps capture degraded and strict admission blocked.

## Task 1 — Typed evidence and atomic local recovery
Files: contracts/ingest_conflicts.py, service/ingest/operations.py, bridge/telemetry/recovery.py, tests/adapters/test_conflict_recovery.py.
Interfaces: OperationConflict(code, event_sha256); HeadIdentity(sequence, raw_sha256, event_sha256); SpoolRecovery(spool, collector, *, producer_id, allow_recovery=False, journal_limit_bytes=None); inspect_head() -> HeadIdentity|None; recover_head(expected, *, recovery_id, actor_id, reason_code, expected_conflict_code) -> RecoveryReceipt; evidence(recovery_id) -> RecoveryEvidence|None.
- [ ] Write tests for strict gate/selection, evidence binding, successful acknowledgement, all three conflicts, atomicity, retry/ID reuse, capacity, corruption and concurrency.
- [ ] Observe expected missing-API red.
- [ ] Implement typed conflicts preserving historical ValueError messages; implement one-head local recovery, original-byte journal and exact CAS/byte bounds.
- [ ] Run selected unit capture/contracts with zero skips/guard denials.
- [ ] Commit scoped Task 1 code/tests.

## Task 2 — Capture status and real database recovery
Files: bridge/telemetry/spool.py and emitter.py; tests/integration/test_conflict_recovery.py; expand adapter tests.
Interfaces: DurableSpool.quarantined_count() -> int; existing emitter health/may_admit_new_work preserve old no-quarantine behavior and retain degraded/strict-blocked state when quarantine exists.
- [ ] Add failing status and real collector tests before modifying status behavior.
- [ ] Implement read-only count and health/strict-admission checks; leave allow_with_gaps unchanged.
- [ ] Run frozen source through disposable PostgreSQL, covering actual conflict classes, mixed tail drain, original-row immutability and post-commit uncertainty.
- [ ] Run prior focused lifecycle/ingest/history/capture/contract regressions; verify no candidate schema dependency for default legacy ingest.
- [ ] Commit scoped status and integration changes after green.

## Task 3 — Review, documentation and preservation
Files: docs/specifications/permanent-conflict-recovery.md, lifecycle reference cross-link, spec/plan.
- [ ] Explain reported actor versus permission, local typed-evidence limitation, bounded storage, withheld facts, downgrade and later operational activation gaps.
- [ ] One enforced fresh read-only review with before/after hashes/Git state; fix confirmed important findings red→green, ledger deferred minors.
- [ ] Verify code vs tested snapshot, original scope, candidate schema provenance, clean product/index and all lab teardown.
- [ ] Commit only scoped changes, retain scratch receipts/ledger, record direct task completion.

## Execution rulings
User explicitly approved direct implementation of this slice, so design/plan artifacts make that work concrete without repeated approval requests. Keep the requested branch and scratch evidence rather than creating a new worktree or deleting the evidence. No integration menu or operational activation is authorized.
