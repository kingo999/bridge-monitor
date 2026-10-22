# BM-04 isolated validation evidence export

User explicitly approved direct Codex implementation and Git changes on codex/skynet-task-status. Keep validation scratch-only, with no installation, activation or deployment. Product target remains Bridge Monitor; no second router/provider selector.

## Bounded slice
Add an opt-in local read-only export API, no live collector, endpoint, CLI registration, schema migration, pricing, usage-history backfill or policy change. Reuse existing event/lineage tables and operation_snapshot. Read selected task IDs in a single PostgreSQL repeatable-read/read-only transaction. Snapshot time is current transaction time, not a historical database reconstruction or deployed-source attestation.

Verify one local capture mapping: ClaudeCLIAdapter's terminal result usage -> usage.reported, provenance claude_cli_result, scope execution, is_cumulative True. Keep input_tokens, output_tokens, cache_read_input_tokens and cache_creation_input_tokens separate exactly as captured. Verify with synthetic frames through the real adapter, not a live provider probe. This establishes local capture behavior, not external billing semantics/provider identity. No total_tokens or cost/savings inference.

Reconcile once per distinct model_execution_id across all selected work-item/call lineage, including parent/child, production and shadow. Repeated cumulative snapshots are not summed. Sort by occurred_at; identical observation-ID facts deduplicate, conflicting reuse rejects usage. Ambiguous same-time updates, counter regression, mixed producer/origin, invalid counters, noncumulative or unsupported scope/provenance, unresolved ownership or partial capture remain unknown. Preserve partial fields in a valid latest snapshot without filling them from earlier updates. Missing is never zero.

## Export contract
Versioned metadata-only JSON: explicit source_ref, selected/found task IDs, current snapshot time, work/call/attempt/model lineage, job admission/reservation metadata, decision route/policy/reasons, results and reported disposition evidence, per-execution reconciled counters, per-field known subtotals/coverage/qualified totals, operation projections, and stored-event ID/hash manifest with canonical export evidence digest.

Default allow_export=False; actual bool required. Task IDs unique, nonblank <=256 chars, 1..16 selected. source_ref nonblank <=256. Global fetch budget max_rows default 2000, strict 1..10000 across all queries, operation histories included; excess raises rather than truncates. At most 16 operation projections; encoding has a fixed 16 MiB bound and no filesystem writes. Snapshot query never provisions absent candidate tables and labels operation schema absence explicitly.

All source events in selected recorded lineage are represented in the bounded manifest; SQL extracts only allowed usage fields, never raw prompts/answers/content bytes, request JSON, arbitrary gate facts or disposition prose. Decision reasons are emitted only for a finite set verified from current router eligibility code; other reason text is represented by hash and unknown/redacted status. Runtime provider stays unknown without independent evidence. Requested/reported identifiers remain distinct; differing aliases alone are not a wrong-model verdict.

Denominators are explicitly recorded evidence: selected/found tasks, recorded/admitted/dispatched calls, recorded model executions, delivered results, reported/unknown/ambiguous acceptance and usage coverage. Reported acceptance is not independent quality validation; no trust is inferred from a model judging itself. Incomplete lineage (missing tasks, dispatched calls without models, direct work unsupported by this mapping, unresolved usage/invalid correlation) prevents complete lineage totals. Known-model subtotals remain labelled. Reservations remain budgets. All billed cost, savings and counterfactual fields remain null with reasons.

## Data/compatibility boundaries
Current normalized domain tables describe snapshot-visible lineage, not historical state. Export source hashes are recorded provenance, not signed provider receipts or tamper-proof database evidence. Cross-task parents are not recursively fetched. Unknown/cyclic/inconsistent lineage remains explicit. No automatic attribution of unscoped global capture signals. Operation timings reuse the tested projection and remain producer-clock observations; fixture origins and missing capture stay visible.

No existing contract/schema registry, parser, ingestion, runtime consumer, query endpoint or operational configuration is changed. All additions stay disabled unless the local caller opts in and already has selected-task database read authority.

## Acceptance and rollback
Unit red/green: actual adapter capture, strict counters, cumulative updates, duplicate identity/conflict, mixed/unsupported sources, partial fields, monotonic regression, zero versus unknown, same-time ambiguity, separate cache fields, bounded deterministic serialization.
Real disposable PostgreSQL: lineage across parent/child/shadow, model evidence ambiguity, explicit route reasons, reported acceptance uncertainty, missing/direct/orphan usage, actual operation timing, absent candidate schema, denied/default before connect, global bounds/no truncation, read-only enforcement, coherent MVCC snapshot and no source/data mutation. Selected prior contract/ingest/lifecycle/recovery/history tests remain green.

Use existing PostgreSQL binaries, fresh loopback scratch cluster, frozen source, per-connection identity checks and blocked external network/provider/subprocess execution in fixtures. Retain evidence, stop only owned instances, remove lab credentials. Enforced read-only final review with source/Git snapshots. Rollback is a dependency-aware revert of new files; no operational migration/activation is performed.

Suggested execution model: Claude Sonnet, advisory because the slice extends established reporting/capture contracts with focused tests. Preserve the chosen model.
