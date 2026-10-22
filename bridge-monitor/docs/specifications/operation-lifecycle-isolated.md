# Opt-in operation lifecycle pipeline

This slice extends Bridge Monitor's existing telemetry pipeline for isolated compatibility validation. It does not enable any operational producer, client endpoint, route, policy or service. The existing lifecycle contract remains [candidate-operation-lifecycle.md](candidate-operation-lifecycle.md).

## Explicit support

Construct both `DurableSpool(..., allow_operations=True)` and `TelemetryEmitter(..., allow_operations=True)` to emit candidate observations. Flags require actual booleans and default to False. An enabled emitter requires an enabled spool. A disabled emitter encountering a candidate head from an enabled spool retains it and marks capture unhealthy rather than delivering it.

`emit_operation(event_type, *, operation_id, operation_kind, task_id, work_item_id, payload, origin, capture_completeness, **correlation)` validates against the reviewed reader before allocating a producer sequence. It owns event ID, producer identity/incarnation, sequence, timestamp, observation layer and schema version. Correlation accepts only session/call/attempt/decision/parent-operation/model-execution/worker/parent-event IDs and requested/reported model/config version. The method records an observation; it never runs the described operation. Its boolean result means spooled, not necessarily committed or executed.

The collector must explicitly invoke `ingest_batch(..., allow_operations=True)`. No existing operational collector binding does so in this slice. Invalid candidates, wrong authenticated producer and candidate batches carrying content are rejected before connecting. Historical events retain their original hash, validation, domain/status projection and publication behavior. Candidate batches may mix historical events when no content accompanies them.

## Storage and transaction semantics

The additive SQL artifact is `database/candidates/operation_lifecycle_v2.sql`. It is outside ordinary migration discovery and has no automatic application path. The lab applied it explicitly, twice, after ordinary migrations. Applying it operationally remains a separate activation/migration decision.

Candidate records reuse the `events` and `producer_sequences` tables. `operation_event_facts` retains the complete reviewed envelope and links it to the stored event/hash; `operation_identities` fences immutable operation lineage. All are in the existing database, with no second store or coordinator. Operation IDs are globally scoped within that database. A different producer/incarnation, kind, task/work or execution correlation under the same operation ID is a conflict. Call and other correlation IDs are reported identifiers; this slice does not verify their domain references or grant execution authority.

Sorted per-operation transaction locks and an identity upsert serialize concurrent lineage checks. Same event ID and canonical hash is a duplicate; event mutation, reused producer sequence or changed operation lineage aborts the enclosing transaction. Shared event/sequence identities also fence cross-family collisions. No successful operation is repeated to repair capture: after a lost post-commit acknowledgement, replay receives duplicate and only then removes the spool entry.

Candidate ingest deliberately omits legacy status/domain projection and publication. It does not increment dashboard capacity or expose candidates in the existing publication stream. It also does not fabricate task/call domain rows. Older readers cannot be assumed compatible with candidate-bearing storage merely because publication is unchanged.

The current work-detail and exchange-inspection queries exclude the reserved operation. event family before applying head/tail and inspection limits. Candidate facts therefore cannot appear in those legacy event lists or displace historical rows from their bounded windows. Other current query paths already select named historical event types or the legacy publication stream. Arbitrary older binaries and direct SQL readers are not covered by this exclusion.

## Bounded projection

`service.queries.operations.operation_snapshot(connection, operation_id, *, allow_operations=False, max_events=1000)` reads one operation's actual persisted facts, validates each envelope/hash and projects them in deterministic occurred-time/event-ID order. No row returns None. `max_events` requires an integer in 1..10000; oversized history raises instead of silently truncating. This is a local query API with no HTTP/SSE/MCP/dashboard registration.

The snapshot contains immutable identity, state, distinct definitive outcome kinds, uncertainty flag, observation IDs/counts, capture states/origins, verification and delivery fact lists, elapsed milliseconds and timing state. Definitive completed/failed/cancelled kinds that disagree yield conflicted. Without a definitive outcome, unknown precedes started, otherwise state is unobserved. Verification and delivery never establish execution success. An unknown observation remains visible even if a definitive outcome arrives later; this projection cannot release held execution ownership or authorize a retry.

Timing requires complete capture, a single origin, no unknown observation, exactly one observed start, exactly one definitive outcome and nonnegative timestamp order. Otherwise elapsed_ms is null with capture_incomplete, mixed_origins, uncertainty_observed, missing_observed_start, ambiguous_outcome or clock_order. Synthetic/imported origins remain explicit; these fixture timings are not observed production routing efficiency. Repeated same-kind terminal observations remain evidence and suppress unambiguous timing. Verification/delivery lists are observation facts, not deduplicated semantic receipt counts.

Elapsed time uses the producer's occurred_at clock, not an independently verified monotonic execution span. Immutable operation identity requires the same producer/incarnation, but positive clock adjustments within that producer can still distort elapsed time. This slice does not attest clock accuracy.

## Verified boundary and remaining work

| Consumer | Support in this slice |
| --- | --- |
| Default spool/emitter/store | Historical behavior; no candidate activation |
| Explicitly enabled spool/emitter/store | Seven candidate types and mixed historical queues |
| Real operation_snapshot query | Bounded candidate projection from persisted facts |
| Legacy publication/status | Historical events only; candidate effects excluded |
| HTTP, SSE, dashboard and live producers | No candidate integration or activation |

Focused tests cover strict opt-in, owned envelope fields, overflow, disabled/restart replay, real persistence/projection, loss after commit, duplicate/hash recovery, conflicting IDs/sequences/lineage, out-of-order facts, cancellation/unknown/conflicting outcomes, capture/timing limits and concurrent writers. Historical capture, event contracts, ingestion, content, replay and lineage regressions are selected alongside them. This does not establish complete BM-02 closure, deployed-source parity, arbitrary older-binary reads, operational retention of the additive tables or live routing value.

## Isolation, disablement and rollback

Validation uses a frozen source copy, scratch SQLite and an independently owned PostgreSQL instance from existing binaries, loopback-only and without a registered Windows service. Fixture connections verify exact lab identity; source writes, non-lab databases, external network and fixture subprocesses are blocked. These controls are test harness safeguards rather than an OS sandbox.

The operational launchers, configuration, automatic migration chain, routing policy and services remain unchanged. All constructor/store/query defaults are disabled. No deployment or activation occurred.

A scratch recovery test demonstrates that a disabled emitter retains queued candidate facts, and an explicitly enabled reader/collector can recover them after a lost acknowledgement. Future operational disablement must stop new candidate producers and preserve/drain or reconcile candidate-bearing queues with capable consumers. Reverting binaries while candidates remain queued or stored is not a guaranteed downgrade. Revert only this slice's commits after checking dependencies, retain additive evidence and do not drop tables, discard queued records or re-execute external work to repair telemetry.

Permanent event/sequence/operation-identity conflicts also retain the head and block later records, including historical ones. Adding a previously absent optional identity field (for example call_id) is a conflict, not late enrichment. The emitter marks capture degraded; reject_new_work admission fails closed. There is no automatic quarantine, skip or conflict repair. On such a condition, stop the affected producer, preserve the queue and conflicting stored evidence, inspect the producer's ID/sequence/lineage assignment, and plan a separately reviewed recovery that retains that evidence. Correcting future producer IDs alone does not unblock an already queued conflicting head. Do not edit/delete/acknowledge it blindly or re-run the underlying operation. An operator recovery/quarantine workflow remains a prerequisite to later operational activation, not a hidden capability of this slice.
