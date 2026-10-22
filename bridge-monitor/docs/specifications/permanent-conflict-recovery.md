# Explicit permanent-conflict queue recovery

This is an isolated local API inside Bridge Monitor. It recovers a telemetry queue by retaining a confirmed conflicting candidate record outside the active queue. It does not repair contradictory observations, repeat an operation, release execution ownership or enable an operational recovery workflow. No MCP/HTTP endpoint, CLI command, runtime caller, launcher or policy activates it in this slice.

## Selection and authority

`SpoolRecovery(spool, collector, *, producer_id, allow_recovery=False, journal_limit_bytes=None)` requires actual boolean opt-in and a candidate-enabled DurableSpool. Construction does not create recovery tables. Its collector must be the trusted existing telemetry collector, supporting local hash-bound typed rejections. It is not a generic tool or operation executor. A remote transport that flattens exceptions is unsupported.

Actor IDs in receipts are reported audit metadata. The API does not authenticate an operator or grant permission to read/write a spool. The local caller must already have authority to inspect that specific spool and use that producer's collector. File access protection and later operational authentication remain deployment concerns. No credentials should appear in metadata or exception messages.

First stop affected producers as appropriate and inspect the active record with the existing capable spool reader. `inspect_head()` returns a frozen HeadIdentity(sequence, raw_sha256, event_sha256) for a valid candidate belonging to the configured producer, or None for an empty queue. It refuses historical, malformed, content-bearing or wrong-producer heads. The raw hash covers the sequence, exact event/content strings and size, using a domain-prefixed SHA-256 of their compact JSON array; the event hash is the existing canonical candidate hash. Selecting one record does not authorize a later head.

## One explicit action

In an authorized isolated lab, after inspecting the exact head:

```python
from bridge.telemetry.recovery import SpoolRecovery

recovery = SpoolRecovery(spool, candidate_collector,
                        producer_id="lab", allow_recovery=True)
head = recovery.inspect_head()
receipt = recovery.recover_head(
    head, recovery_id="lab-review-001", actor_id="lab.operator",
    reason_code="confirmed_conflict",
    expected_conflict_code="operation_identity_conflict",
)
evidence = recovery.evidence("lab-review-001")
# Only after receipt confirmation, explicitly drain the remaining queue:
emitter.replay()
```

The caller supplies a stable recovery ID, nonblank actor/recovery identifiers (up to 256 characters), a reason code matching `[a-z][a-z0-9_.-]{0,127}` and one expected conflict code. Under the existing drain lock, recovery checks prior ID use, compares the current head and producer, reserves journal capacity and probes the telemetry collector once. Ordinary emit/replay never invokes recovery automatically.

| Collector outcome | Recovery result |
| --- | --- |
| Matching OperationConflict with event_id_conflict, producer_sequence_conflict or operation_identity_conflict and current canonical hash | Atomically retain as quarantined and remove this active head |
| One committed or duplicate receipt | Atomically retain as acknowledged and remove this active head; never quarantine a successful commit |
| Plain textual ValueError, authentication/validation failure, transport error, unknown/mismatched typed evidence or ambiguous receipt | Refuse; leave active head and tail intact |

OperationConflict remains a ValueError subclass with the historical error text. Only candidate ingest produces these structured errors in this slice. Real ingest unwinds its transaction before the collector rejection reaches recovery. The type/hash is local evidence from a trusted collector, not a signed receipt or proof against a malicious collector.

## Durability and bounds

The same SQLite spool receives an additive spool_recoveries table only when an action is successfully persisted. A BEGIN IMMEDIATE transaction rechecks the head, inserts exact original event/content strings plus structured request/receipt, then conditionally removes that same active head. Failure rolls both operations back. PostgreSQL and SQLite do not share a distributed transaction: a successful collector commit followed by local failure leaves the head for duplicate acknowledgement on retry.

Receipts include recovery ID, action, original sequence, raw/canonical hashes, reported actor/reason, expected and actual conflict codes, collector acknowledgement when present and an aware UTC recorded_at timestamp. They imply no execution success for quarantined observations. `evidence(recovery_id)` reads the retained original strings and immutable receipt and checks their stored integrity. An identical recovery-ID retry returns the original receipt without probing or acting on the tail; changed reuse refuses. Keep the same ID after a lost local completion response.

Integrity checks cover the original strings, candidate hash, receipt identity/action columns and logical byte length. They do not cryptographically attest actor/reason/request metadata or prevent privileged SQLite edits/deletion. Count and capacity queries trust stored rows. Actor/recovery identifiers preserve their literal bounded strings, including whitespace/control characters; any later display/logging surface must escape them. These are trusted-local audit records, not tamper-proof evidence or authenticated actor claims.

Journal logical payload bytes (raw strings plus structured metadata) have a finite bound, defaulting to the configured spool limit and never exceeding it. Recovery reserves the larger possible receipt representation before probing, then checks actual bytes again in the write transaction. A full journal refuses without probing. Active queue and journal bounds are separate, so their combined logical payload budget can reach twice the spool limit. SQLite pages, indexes, locking and WAL overhead are not a hard disk quota. No automatic journal deletion, retention, requeue, rewrite, bulk quarantine or recovery-ID recycling is provided.

## Capture and admission remain qualified

`DurableSpool.quarantined_count()` reads existing journal actions without provisioning schema. After the tail drains, quarantined evidence still makes emitter health degraded and adds a quarantined count. Strict reject_new_work admission remains false. The previously approved allow_with_gaps behavior is unchanged; recovery does not select or modify that policy. No-quarantine health output retains its existing shape.

known_gaps keeps its prior overflow semantics. A quarantined record is separately retained withheld evidence, not a successful ingestion, a resolved operation or automatically declared data loss. There is no disposition API to relabel quarantine healthy in this slice. Thus queue obstruction is recovered while the evidence conflict remains explicit.

When quarantine and overflow coexist, replay can report the overflow gap but suppresses capture.recovered while quarantine remains. Without quarantine, historical overflow reporting is unchanged. The shared drain lock coordinates cooperating emitter/recovery callers; direct acknowledge or SQLite mutation does not automatically acquire it. Such callers must honor that lock, and the recovery transaction additionally rejects a changed head. A slow collector can still make another drainer hit the existing ten-second lock timeout.

## Validation and rollback boundary

Tests use scratch SQLite and a fresh owned loopback PostgreSQL instance from existing binaries. They cover head/hash/producer fencing, typed evidence, exact bytes, metadata limits, capacity, atomic rollback, lost local completion, idempotent retries, concurrent recovery, normal replay fencing, real candidate conflicts, immutable committed facts and tail delivery. Historical capture, contract, ingest, lifecycle and history regressions remain selected. No operational spool, live database, real provider or HTTP/SSE/browser validation is used.

The operational bridge remains unactivated. Future deployment still needs a reviewed operator/authentication surface, transport-specific typed evidence, access/retention policy and appropriate broader fault evidence. The API is ordinary trusted local code, not an OS or permission sandbox.

Rollback means a dependency-aware revert of this slice after preserving recovery-bearing SQLite files. Older binaries do not understand quarantine counts or the audit journal and cannot be assumed safe for those files. Stop affected admissions and inspect/export retained evidence with capable code before any later downgrade. Never discard a queued or quarantined record, mutate its identity or re-execute an underlying operation to fix telemetry.
