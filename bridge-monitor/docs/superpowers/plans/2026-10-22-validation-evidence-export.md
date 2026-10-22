# BM-04 Validation Evidence Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline with test-driven-development and verification-before-completion.

**Goal:** Produce a truthful bounded metadata export from existing Monitor evidence.
**Architecture:** New strict measurement contract and pure per-execution reconciliation, then an explicitly enabled snapshot reader and deterministic serializer. Reuse existing operation projection. No operational wiring.
**Tech Stack:** Existing Python/Pydantic/pytest/psycopg/PostgreSQL 18; no installation.
**Spec:** docs/superpowers/specs/2026-10-22-validation-evidence-export-design.md

## Global Constraints
- Direct Codex approved on codex/skynet-task-status, scratch-only validation; no activation/deployment/push/merge.
- No original product file changes or automatic schema/endpoint registrations.
- Four captured counters stay separate; unknown usage/provider/cost/savings stays unknown.
- Global budget 1..10000 rows (default 2000), 1..16 task IDs, <=16 operations, serialized JSON <=16 MiB.
- Suggested execution model: Claude Sonnet, advisory for an established reporting extension with focused tests; chosen model unchanged.

## Review Focus
- Duplicate cumulative observations must never inflate totals, including parent/child/fallback and shadows.
- Missing/invalid/partial/mixed provenance must not become zero or falsely complete totals.
- Current-domain model aliases and reported acceptance must not become verified provider/quality claims.
- Snapshot reader must not write, provision schema, leak raw text or silently truncate.
- Source/operation evidence must come from one read-only MVCC view with explicit bounds.

## Task 1 — Strict local usage mapping and reconciliation
Files: contracts/measurement.py; service/queries/usage_reconciliation.py; tests/contracts/test_measurement.py; tests/fixtures/measurement/claude-result.json.
Interfaces: UsageSnapshot strict/frozen model; reconcile_execution_usage(execution_id, observations)->dict; summarize_usage(executions, *, incomplete_lineage=False)->dict.
- [x] Write failing mapping/reconciliation fixtures and tests first; prove actual ClaudeCLIAdapter mapping with fake frames, no subprocess/provider.
- [x] Observe missing-module red; implement strict nonnegative counters and exact local mapping.
- [x] Test chronological cumulative latest snapshot, duplicate/conflicting IDs, same-time ambiguity, regressions, missing/zero/partial values, source/capture/scope mismatches, separate cache semantics and aggregate coverage.
- [x] Run focused new/existing contract and safe adapter mapping tests; commit Task 1.

## Task 2 — Bounded read-only snapshot export
Files: service/queries/validation_export.py; tests/integration/test_validation_export.py; expand measurement tests for serialization.
Interfaces: build_validation_export(dsn, task_ids, *, source_ref, allow_export=False, max_rows=2000)->dict; encode_validation_export(report)->bytes.
- [x] Write failing gate/serialization/actual PostgreSQL tests before implementation.
- [x] Implement one repeatable-read/read-only snapshot, allowlisted SQL projections, bounded fetch budget, lineage/counter/denominator/operation data and evidence digest.
- [x] Preserve unknown mappings, reported-only acceptance, distinct model evidence and current-snapshot semantics; no arbitrary content export.
- [x] Freeze source and run disposable PostgreSQL plus prior focused regressions. Verify absent candidate schema, no database changes, MVCC/bounds and complete owned teardown.
- [x] Commit Task 2 after green evidence.

## Task 3 — Reference, read-only review and preservation
Files: docs/specifications/validation-evidence-export.md plus this spec/plan.
- [x] Document exact scope, counter mapping/provenance, definitions, omitted data, opt-in API, current snapshot, limits and rollback.
- [x] One fresh enforced read-only review; verify source/Git before/after; important fixes red→green, ledger deferred minors.
- [x] Verify original files unchanged, tested code/source match, clean product/index, unchanged candidate provenance and lab teardown.
- [x] Commit only scoped additions; retain scratch receipts and record direct execution completion. No live collection or activation.

## Execution rulings
Fresh direct execution approval was explicitly received after the Bridge-default routing reminder. The approved scope selects inline implementation; do not repeat design/plan approval requests. Keep the existing requested branch and retain scratch evidence instead of creating another worktree or deleting receipts. No integration menu or deployment is authorized.

## Validation record
Task 1: 156 focused tests passed. Task 2: 291 selected tests passed in lab-export-2. Review 8a153977-0806-44e2-a45f-9a004631fca5 completed enforced read-only with source/Git unchanged. Parent Codex independently confirmed acceptance/lineage and cross-model observation-ID gaps; 10 + 3 expected regression failures were reproduced in fresh disposable labs before fixes. Final lab-final: 308 passed, zero failed/skipped/guard denials, 599 identity-checked PostgreSQL connections, frozen source unchanged, owned cluster stopped and plaintext credentials removed. Original 454 tracked product files remain unchanged. No activation/deployment/push/merge. Retained detailed receipts in the task scratch BM-04 directory.

Review limitations: reported model unavailable; generic review binding unbound/no_reviews. Findings text was available, not an automated final-code approval. Parent verified corrections with fresh frozen-source tests. Hash dictionary confirmation, authorized metadata fields and producer-clock ordering remain documented boundaries. Selected-cohort reported acceptance is not authoritative final survival.
