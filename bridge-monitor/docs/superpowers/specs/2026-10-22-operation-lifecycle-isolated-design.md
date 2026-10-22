# Isolated operation lifecycle design

User-authorized scope: direct Codex implementation and Git changes on codex/skynet-task-status. Preserve the operational bridge; no activation/deployment. Extend the existing emitter, spool and ingest rather than adding a router, bridge, service or database.

## Design
DurableSpool and TelemetryEmitter gain strict boolean allow_operations=False options. Enabled replay uses the reviewed reader. emit_operation creates validated observations with the emitter's identity/sequence and explicit origin/capture metadata. It never executes an operation. Disabled emitters cannot deliver candidate records from an enabled spool.

ingest_batch gains strict allow_operations=False. Known candidate models are revalidated before connecting; disabled/invalid candidates and candidate content batches are rejected before any write. Historical validation and hashes remain unchanged. Candidate events share events and producer_sequences, retaining ID and sequence conflict semantics. Full candidate envelopes are recorded in an additive operation_event_facts table keyed to events. The SQL lives in database/candidates outside automatic migration discovery, applied explicitly only to a disposable lab database.

Candidate ingest does not call legacy domain/status projection or publish candidate records into the legacy publication stream. Historical events still use the existing path. A bounded operation_snapshot query projects real persisted candidate facts through the existing query package. This is a first operation projection, not HTTP/SSE/dashboard integration.

All candidate locks for a batch are acquired in sorted operation-ID order. Operation identity (producer/incarnation, kind, task/work and optional execution correlation) is immutable across observations. Received time and requested/reported model are not operation identity. Same-ID identical events return duplicate; mutations and producer-sequence/operation-identity conflicts roll the batch back. This is an observational correlation check, not verification that reported task/call IDs exist or authority to act.

## Projection semantics
One snapshot represents one operation. It retains ordered observation IDs, outcome counts, verification and delivery facts separately. Definitive outcomes completed/failed/cancelled conflict if different kinds coexist. Unknown observations remain visible; a later definitive outcome can describe the observed state without authorizing release/retry. Verification/delivery alone never establish execution success.

Elapsed time is available only for exactly one observed start and exactly one definitive outcome, all capture states complete, no unknown observation, no conflicting identity/outcome, and nonnegative timestamp order. Otherwise it is null with an explicit reason. Synthetic origin remains labelled. Ordering is deterministic and independent of arrival order. Exceeding max_events (default 1000, strict 1..10000) rejects rather than silently truncating. No task usage/cost/savings are inferred.

## Acceptance
Exercise real emitter -> scratch SQLite spool -> real ingest -> disposable PostgreSQL -> real operation query. Cover seven variants, mixed legacy/candidate queues, default refusal, outage/restart, commit-before-receipt loss, duplicates, conflicting IDs/sequences/identity, transaction rollback, out-of-order facts, uncertainty, capture gaps and separate verification/delivery. Preserve historical tool.completed meaning/hash. Assert no candidate publication/status/capacity effects. Apply SQL twice in lab to establish idempotence. Exercise concurrent identity/sequence writers with bounded synchronization.

## Isolation and rollback
Use existing PostgreSQL 18 binaries with fresh loopback-only instance, generated lab credentials, no service registration and data/logs entirely in scratch. Verify each connection's host/port/role/database/data-directory. Freeze source for database validation; block writes to source, external databases/providers and subprocesses in fixtures. Keep failed-run evidence. Stop only the owned instance, remove plaintext credentials and verify listener absence.

Operational launcher/config/policy, automatic migrations, clients and services remain unchanged. No runtime producer is wired in. Preserve unrelated Git state and commit only scoped files. Rollback is a dependency-aware revert of this slice; disable future candidate admissions before any later operational rollback, retain facts and queued records, and never replay execution to fix telemetry.

Suggested execution model: Claude Sonnet. This extends established telemetry patterns with focused compatibility tests. Advisory; the user's chosen model is unchanged.
