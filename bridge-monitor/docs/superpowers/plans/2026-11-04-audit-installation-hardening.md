# Audit installation hardening

The user authorized repair of the privileged installation, with the audit task
stopped and disabled until reviewed. Activation is outside this repair.

Root cause: SYSTEM ran a writable supervisor, virtual environment and checkout.
Changing only the runner ACL would leave interpreter/import/dependency paths unsafe.

Chosen repair: privileged producer and ordinary-user consumer. SYSTEM runs only
the native reader and supervisor copied into an administrator-owned, protected
`C:\Program Files\BridgeMonitorAudit`. Its metadata spool is writable only by
SYSTEM/administrators and readable only by the named operator. Python, SQLite,
policy and attribution stay in the ordinary-user consumer. No privileged input
is read from the checkout or user-controlled configuration. Task security prevents
ordinary-user edits or enabling the SYSTEM task. Both definitions are disabled at
registration; no collector is launched by installation or verification.

Suggested execution model: Claude Opus, advisory, because the Windows filesystem,
task and process trust boundaries need consequential security reasoning.
Execution remains direct Codex under the existing audit repair authorization.

- [x] Add bounded, checkpointed spool ingestion and tests for partial lines,
  restart, replacement/truncation and database contention. Never execute records.
- [x] Add the protected native supervisor, disabled-only installer, security
  verification and ordinary-user task; reject reparse points and unexpected ACLs.
- [x] Test without capture; install via explicit UAC elevation, preserving logs.
- [x] Verify protected ownership/DACLs, file hashes, task definitions/DACLs,
  ordinary-user write denial and absence of collector processes.
- [x] Obtain an enforced read-only review and preserve before/after snapshots.
- [x] Update the operator guide and existing context topic/index with evidence;
  retain disabled status regardless of review outcome.

Tradeoff: spool delivery is at least once across crashes, and disk retention is
operator-managed. No automatic deletion or privilege grant to the user is added.
Future activation must explicitly enable both producer and consumer after review.

## Authorized review follow-ups

On 2026-11-04 the user explicitly approved fixes for review
`4a882574-610c-4dc3-904a-812eb220244a`, stronger verification/build evidence,
and continued stopped/disabled status for both tasks. Execution remains direct.

- [x] Reject elevated verifier execution and unsafe report paths; test output guards.
- [x] Verify both complete task XML/action/principal/trigger/settings/DACL sets.
- [x] Rebuild twice, bind source/tool/log/output evidence, and pin protected update.
- [x] Preserve/review bootstrap source, actual command/payload and staging ACLs.
- [x] Close transient ownership races with atomic protected file/directory creation.
- [x] Record ordinary token/masks/positive read controls and effective task rights.
- [x] Capture administrator process/ETW and bounded startup-route evidence.
- [x] Fix session-name overflow and checkpoint recovery; add missing functional tests.
- [x] Reconcile stale docs/context and document retention/recovery without activation.
- [x] Final enforced read-only follow-up review and unchanged-source verification.

Final review `48efeb96-1217-4123-8461-5b521514fd9a` found no blocker to this
disabled repair. Parent reconciled legacy ACL/creation coverage and the old
scheduler LastRunTime with original receipts. No implementation changes followed
review. Activation still needs a controlled build/dependency environment, ETW
session-control checks, supervisor gap/round-trip work, retention and live tests.

Ruling: record administrator ownership and unprivileged-token access, not a claim
to resist an explicitly elevated administrator. Ruling: retain partial protected
updates as evidence with tasks disabled; no destructive automatic rollback.
Retention limits and live trace/reboot tests remain outside this authorization.
