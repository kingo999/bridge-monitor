# Isolated permanent-conflict recovery design

User authorizes direct Codex implementation and Git changes on codex/skynet-task-status. Preserve the operational bridge; no activation or deployment. This slice recovers the telemetry queue, not the underlying operation or the truth of a conflicting observation.

## Selected approach
Use one explicit local SpoolRecovery API beside the existing DurableSpool, not an automatic retry/quarantine policy or a new service/CLI. An operator selects an immutable head snapshot and a stable recovery ID, reported actor, bounded reason code and expected conflict code. Recovery requires allow_recovery=True, an operation-enabled spool, and matching producer identity. No runtime caller, MCP endpoint, route, launcher or operational configuration enables it.

Candidate ingest raises a ValueError-compatible OperationConflict for the three existing permanent conflicts, carrying a static code and canonical incoming-event hash. It retains the original exception text. This is typed local collector evidence, not a signed remote receipt or authorization. Authentication/validation errors, textual ValueErrors, transport failures and mismatched typed evidence cannot authorize quarantine.

Under the existing drain lock, recovery checks the requested ID for an identical prior receipt, compares the current head's sequence and exact raw hash, validates the candidate, then probes the existing telemetry collector once. Committed/duplicate acknowledgement produces an acknowledged recovery, not quarantine. Only matching typed conflict evidence produces quarantine. No underlying operation is run. Failure/uncertainty preserves the active head.

A single SQLite transaction retains the exact original event/content strings plus a timestamped audit request/receipt in spool_recoveries and removes that exact active head. Check head again under BEGIN IMMEDIATE; insertion and removal are atomic. Retained evidence has no automatic deletion or requeue path. Repeated identical recovery ID returns the original receipt without touching the tail; mismatched reuse fails. A lost local completion response is recovered by that same stable ID.

No recovery table is created by default construction, inspection or refused probes. The journal has an explicit logical byte bound, defaulting to the spool limit and never exceeding it. Preflight reserves the larger possible metadata outcome before probing; recheck before commit. Queue and journal bounds are separate (at most twice the configured spool logical payload bound); SQLite/WAL/index overhead is not a hard disk quota.

## Capture and admission
After quarantine, a caller may explicitly replay the remaining queue with the normal capable emitter. Quarantined_count remains visible through capture health; capture stays degraded and reject_new_work refuses new work. The existing allow_with_gaps policy is unchanged. Quarantine is retained withheld evidence, not a committed observation, overflow loss or resolved execution. known_gaps keeps its historical overflow semantics. No resolution/permission/usage/savings is fabricated.

Health checks read journal counts without provisioning tables. Existing default health output is unchanged when no quarantine exists. There is no automatic skip, bulk action, evidence mutation, producer-ID repair or admission-policy change.

## Acceptance
Scratch SQLite tests: strict defaults/metadata, absent schema on refusal, stale/wrong producer/head mismatch before probe, typed evidence binding, transient/ambiguous responses, exact bytes, audit idempotence and conflicting reuse, capacity, transaction rollback, lost local receipt, concurrent recovery/replay, journal integrity checks, tail retention and degraded strict admission.
Disposable PostgreSQL tests: actual event-ID, sequence and operation-lineage conflicts yield typed evidence after rollback; quarantine preserves the original stored facts; mixed tail drains once; duplicate-after-lost-commit is acknowledged rather than quarantined; actual transport failure cannot remove head. Run focused historical capture/contract/ingest/lifecycle/history regressions.

Use existing PostgreSQL binaries, a fresh loopback scratch-only instance, frozen source and per-connection identity checks. No installation, live database tests, provider execution or operational service changes. Retain all evidence and verify owned teardown.

## Alternatives and rollback
Automatic quarantine was rejected because it would silently classify failures and alter operational replay. Editing conflicting records was rejected because it changes evidence and identity. Explicit atomic retention makes the reviewed action inspectable and retry-safe.

Rollback is dependency-aware revert of this slice. Preserve any recovery-bearing SQLite files; old binaries do not understand their quarantined counts or journal. Stop affected admissions and inspect retained evidence with capable code before any future downgrade. This task never applies operational recovery.

Suggested execution model: Claude Sonnet. Existing spool and transaction patterns plus focused fault tests define the work. Advisory; chosen model is unchanged.
