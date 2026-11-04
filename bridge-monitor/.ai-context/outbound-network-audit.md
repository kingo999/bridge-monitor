# Outbound network audit

**Audit UI deployed, 2026-11-04.** Accepted PostgreSQL design implemented under explicit direct Codex approval. Migrations 015/016 add typed search projections, append-only dependency invalidations and operator-session result snapshots. Codex/Claude links preserve exact overview cohorts. Audit has execution and network lists with full retained search, filter/sort, pages and captured evidence. The health timestamp is capture-consumer processing time, not reader liveness or indexer time. Background projection runs in the dashboard lifecycle. Current importer behavior and host capture authorization remain governed by the operator guide. Verification and the existing 36 full-suite failures are documented in docs/verification/2026-11-04-audit-ui.md.

**PostgreSQL migration, 2026-11-04.** Migration 014 was applied to the local Bridge database. PostgreSQL retains the five SQLite audit tables; the existing SQLite capture journal is the local buffer. The limited operator task `BridgeMonitor-OutboundAudit-PostgresSync` drains it every minute while logged in. Full row/field comparison and independent ordered digests matched for 13,800 observations, five registrations, 3,845 process-state rows and two empty policy tables. Retry left counts unchanged. Capture tasks remain disabled. See the operator guide below for reconciliation and recovery. Full-history Audit UI work follows this migration.

**Approved on 2026-11-04.** The first release observes off-machine traffic, including LAN traffic, and attributes it to the sending process and agent run when evidence supports the link. Destinations are generic. Hostnames and model names are optional and have distinct sources.

Correlate host connections with application calls. Preserve unknowns, keep sensitive payloads out of logs, and distinguish an attempted connection from an observed transfer. Operators can replay a draft allowlist and evaluate it live in observe mode before deciding whether to introduce enforcement.

**Current status on 2026-11-04.** The feature is integrated. Both audit tasks are
stopped and disabled. The unsafe initial SYSTEM installation has been replaced
by protected native production and ordinary-user ingestion. Security-review
follow-ups are authorized and applied; final review found no disabled-repair blocker.
Controlled rebuild and bounded SYSTEM runtime/crash/controller-failure checks now
pass. The operator could stop the test ETW session without elevation, so session
control hardening remains. Retention is selected (30 days/72 hours/5 GB) but not
enforced. Boot/reboot and sustained full-host capture remain untested. The
chronological verification history below does not authorize activation.

See the [approved design and acceptance scenarios](../docs/superpowers/specs/2026-11-04-outbound-network-audit-design.md).

See the [operator guide](../docs/operations/outbound-network-audit.md) for setup, attribution boundaries, provider coverage and draft policy examples.

**Privileged verification on 2026-11-04.** An administrator-token smoke test passed with all requested providers available. The test receiver read the canary and the kernel emitted a matching TCP send for the test PID, destination and 12-byte payload. The reader emitted only the test PID's events. The temporary session stopped afterward. Receipt is in the local ignored `var/privileged-verification/result.json`. This verifies the live kernel send path on loopback; an off-machine receiver comparison is still pending.

A subsequent elevated synthetic HTTP health request to the configured LAN worker at `192.168.0.78:8874` timed out during connect. The off-machine comparison remains unverified. Local failure details are in `var/privileged-verification/lan-result.json`; the temporary reader was stopped in the test's finally block.

**Follow-up fixes on 2026-11-04.** Fixed identity recovery for senders and parents that predate collection, ordered trace-loss markers with identity/flow records, recovery after malformed reader input, and cleanup of the exact reader session after a hang or crash. The generic smoke CLI now accepts an operator-selected HTTP destination and writes a machine-readable result; partial coverage returns exit code 2. The elevated follow-up recovered the existing sender and parent and matched 12 kernel bytes to the loopback receiver with all providers available (`var/privileged-verification/followup-result.json`, administrator receipt `followup-admin.json`). The configured LAN worker still times out, so off-machine receiver verification remains pending. Production activation remains pending.

Final follow-up verification passed 101 Python tests, compiled the Windows reader and passed native manifest fixtures for an existing sender and a new child of an existing parent. No audit trace sessions remained after testing.

**Off-machine verification completed on 2026-11-04.** The user selected `https://www.google.com/` after the configured LAN receiver timed out. An elevated, test-PID-only reader captured a synthetic GET to `www.google.com` at `142.251.155.119:443`. Google returned HTTP 200 over certificate-verified TLS 1.3. Sender PID, exact process creation time and parent PID matched the test process. A Python SSL MemoryBIO independently counted 678 encrypted bytes submitted to the socket (including the TLS handshake); kernel TCP send records for that exact peer and local socket also totaled 678. The plaintext HTTP request was 59 bytes. No response body, headers or packet contents were persisted. Reader exit was 0 and no test trace remained. Receipts are in ignored `var/privileged-verification/google-result.json` and `google-admin.json`; the one-off verifier is `google-smoke.py`. This resolves the pending off-machine send-path verification. Google server-side logs were not available. Review, integration and production activation remain pending.

**Integration review on 2026-11-04.** Enforced read-only review `93059112-1248-4844-97f1-4de05c595c82` (reported Claude Sonnet 5.5) found three P1s. The user explicitly authorized fixes. Fixed native/Python identity resynchronization, bounded unknown-identity messages, event-time root lifetimes and explicit shared boundaries, and nonblocking bounded SQLite queues with overflow evidence. A focused read-only review `3040484c-f6e5-49d9-9643-525b3ec3df4d` found the P1s resolved. Both reviews verified unchanged source during the review. Conditional timestamp concern was checked against Microsoft's default ProcessTrace conversion; cached PID birth is now revalidated. Application observations also include creation time and canonical peer/local IPs.

Final feature suite: 958 passed, 460 skipped, five failures reproduced on an untouched base snapshot. Main checkout before integration: 967 passed, 525 skipped, 22 existing failures (logs in ignored `var/audit-integration-20261104/`). Native cache recovery and PID-reuse fixtures passed. Final privileged Google retest passed again with HTTP 200/TLS 1.3, verified process and parent, and 678 kernel bytes matching independently counted TLS bytes (`google-final.json` and `google-final-admin.json` in that artifact directory). No temporary trace remained.

**Integrated on 2026-11-04.** Main fast-forwarded to `783a504` after the user approved fixes and integration. Integrated suite: 988 passed, 525 skipped, and the identical 22 pre-existing failures. Original unrelated working-tree files and non-audit index entries were preserved. See the [review and integration evidence](../docs/verification/2026-11-04-outbound-audit-integration.md). Collection remains opt-in; operator setup and enforcement are separate steps.

**Historical activation on 2026-11-04 — withdrawn.** The first always-on
configuration ran a writable supervisor, Python interpreter and checkout as
SYSTEM. The user subsequently stopped and disabled it after a valid security
warning. Its activation receipts are historical evidence only; it must not be
restored. The journal and persistent application-hook settings were preserved.

The withdrawn configuration captured a synthetic Google run and recovered after
a collector exit (`var/audit-setup-20261104/`). Those tests do not validate the
replacement installation. No reboot was performed. Enforcement remains future work.

**Security correction on 2026-11-04; supersedes the activation status above.** The
original SYSTEM task executed user-writable supervisor/Python/checkout files,
creating an unsafe privilege boundary. The user stopped and disabled it and
authorized a safe repair, explicitly retaining disabled status until review.
The protected replacement uses an administrator-owned native producer/supervisor
under `C:\Program Files\BridgeMonitorAudit` and an ordinary-user spool ingestion
task. SYSTEM never imports the checkout or opens its mutable SQLite journal.
The operator can read but cannot run/enable/change the producer task. Both task
definitions were registered disabled, and no collector was started by repair.
Forty-three real ordinary-token filesystem access checks denied writes, deletes,
ownership and ACL changes; effective task permissions deny execute/write/delete/
ownership/ACL changes. Native imports are four Windows system DLLs. Read-only
verification confirmed hashes/ownership/DACLs, no collector processes and zero
audit ETW sessions. Spool and existing audit regression tests: 27 passed.
Read-only review `4a882574-610c-4dc3-904a-812eb220244a` found no reusable
unelevated-user-to-SYSTEM execution path, and requested stronger verification.
Both reviewed Git roots were unchanged. The user explicitly authorized follow-ups:
ordinary-token verifier guards, full task checks, both task DACLs read-only,
reviewable protected UAC bootstrap/update/inventory, and token/access evidence
with read controls. The reader was rebuilt twice with signed MSVC tools; both
outputs hash to `F34588F8AA3771A2599198B43447FE674B94FC22238F26AE02005B6540455237`.
The native source matches the previously reviewed integrated source. The protected
update copied this build and the corrected supervisor without starting either task.
The administrator inventory found zero audit processes/ETW sessions, no unreadable
Python/PowerShell command lines, and no additional privileged checkout startup
route in the enumerated tasks/services/Run keys/Startup entries. Other matching
Bridge tasks run Limited and were left untouched. Normal-token evidence includes
161 filesystem checks and 12 effective task checks. Protected file/directory
creation supplies the administrator owner and DACL atomically, including bootstrap
copies and receipts; it does not rely on repairing a temporary user owner.
Current focused tests: 44 passed.
Final read-only review `48efeb96-1217-4123-8461-5b521514fd9a` found no blocker to
the disabled repair and no reusable unelevated-to-SYSTEM route. Both roots and
2,789 parent-snapshotted files were unchanged during review. Parent reconciliation
confirmed legacy inherited DACLs remain safe (fresh-installer atomic creation was
not live-exercised), and producer LastRunTime `12:16:51` belongs to the withdrawn
activation, preceding the first protected install at `12:42:43`. The consumer has
never-run history. Direct raw-source comparison to full reviewed commit
`783a504dd7a3a7f753156e7de389bb9799a46f75` differs only in CRLF/LF endings.
No implementation changes followed final review. Future activation additionally
requires a constrained/recorded build environment and dependency verification,
ETW session-control checks, supervisor round-trip/gap cleanup, retention and live
start/restart/logon/reboot validation. Both tasks remain disabled. No live capture/reboot/restart verification is claimed
for this replacement. See the [repair verification](../docs/verification/2026-11-04-audit-installation-hardening.md).

**Controlled rebuild/runtime checkpoint on 2026-11-04.** Explicit compiler argv
and allowlisted environment exclude inherited compiler/linker options and user
search paths. Sixty-four signed/hashed support binaries and 4,374 SDK/header/library
inputs were recorded; the two builds match installed reader hash F34588F8….
Read-only preflight `2d4c8fd1-01ac-4ff5-8ad9-4cd19111a878` preserved both roots.
The user authorized its deadline/cleanup/pin guards before execution.

A no-trigger, 90-second temporary SYSTEM task, created with protected owner/DACL
atomically, ran the supervisor in an isolated PID/spool scope. Two HTTPS Google
requests returned 200/TLS1.3, each matching 678 kernel bytes to independent TLS
accounting with correct sender birth, parent and agent/run. Forced reader restart
passed. A separate controller-exit-91 drill proved independent watchdog cleanup
at 60 seconds. Both temporary tasks were removed; final checks found zero native
readers/ETW sessions and both production tasks disabled with zero instances and
unchanged last-run history. No production task was started by this checkpoint.
No malformed reader gaps or unavailable providers occurred. Tests: 47 passed,
one optional grpcio skip; access checks: 336 filesystem and 12 task probes.

The unelevated operator queried/stopped the disposable SYSTEM ETW session
(ControlTrace status0); EventAccessQuery returned5. No ACL or group changes were
applied. Before activation, harden/verify ETW control and implement/test selected
30-day journal, 72-hour post-ingestion spool and 5GB budget with pause-and-gap at
capacity, preserving un-ingested evidence. No deletion/cap enforcement was enabled.
See the [runtime report](../docs/verification/2026-11-04-audit-controlled-runtime.md)
and its evidence; local receipts are in ignored `var/audit-runtime-20261104/`.
