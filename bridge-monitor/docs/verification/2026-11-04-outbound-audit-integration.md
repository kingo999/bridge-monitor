# Outbound audit review and integration

Integrated locally into Bridge main on 2026-11-04 by fast-forward to
`783a504`. The user authorized review, fixes, verification and integration.
Direct Codex work continued under the standing task approval. No active or queued
Bridge jobs existed immediately before integration.

## Review and fixes

Enforced read-only Claude review `93059112-1248-4844-97f1-4de05c595c82`
reported three P1s. The user explicitly authorized their repair.

- Identity frames now accompany each network frame, so collector-side gaps do
  not permanently lose process/run attribution. Unknown identities carry an
  upper birth-time bound. Cached process birth is also checked against the live
  PID where available.
- Root lifetime and explicit shared-process boundaries are stored with event-time
  semantics. Rows distinguish direct registration from inferred process lineage.
  Unmarked shared services still require operator identification.
- SQLite contention retains a bounded queue and retries without blocking the
  reader. Overflow is recorded after recovery. Loss of lifetime updates disables
  attribution until restart. Shutdown reports a persistent storage outage.

Focused read-only review `3040484c-f6e5-49d9-9643-525b3ec3df4d` found the
original P1s resolved. Both tools verified unchanged source across review.
The reported reviewer model was Claude Sonnet 5.5. Conditional QPC concern was
resolved against Microsoft's default ProcessTrace conversion; live timestamps
also matched Windows process creation times. Review ran with commands and edits
disabled.

## Verification

| Tree | Passed | Failed | Skipped |
| --- | --- | --- | --- |
| Final feature | 958 | 5 | 460 |
| Main before integration | 967 | 22 | 525 |
| Integrated main | 988 | 22 | 525 |

All five feature failures reproduced on a clean snapshot of base `ec5ab97`.
The integrated main failure set exactly matches its pre-integration baseline.
Existing main failures are context-permission fixtures and shadow CLI path
restrictions. The suite is not fully green; there are no new failures.
Unavailable integration environments account for skips; no full live-system
coverage is claimed.

The Windows reader compiled in both checkouts. Native recovery, parent/child and
stale-PID fixtures passed. The final elevated Google test returned HTTP 200 over
certificate-verified TLS 1.3. Sender and parent matched and kernel TCP sends totaled
678 bytes, exactly matching independent encrypted-byte accounting. No temporary
test trace remained.

Detailed test logs, comparison summary, elevated receipts and preserved original
collision files are under ignored `var/audit-integration-20261104/`.

## Integration state

The pre-existing main working tree was preserved. Only the audit context entry
was updated; other context-index entries retain their original content. The older
audit design and topic were backed up before replacing them with branch versions.
Other uncommitted work remains uncommitted.

Collection remains opt-in through the [operator guide](../operations/outbound-network-audit.md).
Runtime collector setup, retention and audit database access control are operator
steps. The implementation performs observation and policy rehearsal. Enforcement
requires a separately approved design.

## Timestamp reference

[Microsoft EVENT_TRACE_LOGFILEW documentation](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/ns-evntrace-event_trace_logfilew)
states that ProcessTrace converts timestamps to system time by default. This
consumer does not request RAW_TIMESTAMP.
