# Controlled build and ETW/runtime verification — 2026-11-04

**Result:** bounded SYSTEM runtime and failure-cleanup checks passed. Both
production audit tasks remain stopped and disabled, with zero running instances.
No temporary verification task, native reader or audit trace session remains.
This report does not authorize production activation.

The user approved controlled rebuilding/runtime checks, then explicitly approved
preflight cleanup/pin fixes. This was direct Codex work under the audit/Bridge
management authorization; the Bridge MCP server stayed stopped. Direct task
metadata is recorded in Monitor. Provider/model policy was unchanged.

## Controlled build

Compiler invocation uses argv and an explicit environment with fixed MSVC/SDK
paths. Inherited `CL`, `_CL_`, `LINK`, `_LINK_`, `LIBPATH` and user `PATH` are
excluded; no shell or generated batch script is used. Evidence records 64
signed/hashed compiler/support binaries and 4,374 header/library input hashes,
checked again after compilation. Raw source comparison to full reviewed commit
`783a504dd7a3a7f753156e7de389bb9799a46f75` allows only CRLF/LF differences.

Both `/MT /Brepro` builds produced the installed reader's SHA-256:

`F34588F8AA3771A2599198B43447FE674B94FC22238F26AE02005B6540455237`

The controlled manifest and corrected supervisor were installed while both tasks
were disabled. The coordinator checks expected reader/bootstrap hashes before
UAC; a negative test verifies refusal. Privileged copies receive administrator
ownership/DACL at creation and are hash-checked before execution. The manifest is
parsed from its protected copy. This is controlled local provenance, trusting
the installed Microsoft toolchain/SDK and approved source; it is not independent
compiler attestation, a full dynamic dependency trace, or isolation against an
actively compromised build owner.

## Bounded SYSTEM runtime

A temporary `BridgeMonitor-AuditVerification-<run>` task had no triggers, a
90-second limit and protected ownership/DACL supplied at registration. Ownership
was checked before running it. It was separate from both production tasks.
The protected runner checked SYSTEM SID `S-1-5-18`; only the coordinator PID's
network frames entered its private test spool. Ordinary-user ingestion registered
the sender with a synthetic agent/run and wrote a separate test journal.

Two certificate-verified TLS 1.3 GETs to the user's previously selected Google
destination returned HTTP 200. Each independently counted 678 encrypted bytes,
including the TLS handshake, for a 59-byte plaintext request. Kernel sends for
the exact sender/peer/local socket totaled **678 bytes in each phase**. Sender
PID, creation time, parent PID and agent/run attribution matched. No response
headers, bodies, packet contents or credentials were persisted.

Reader PID 36744 was forcibly terminated; the supervisor recovered with PID 23416
after its configured backoff. The second byte/identity comparison passed. Raw
output had two expected start/restart gaps, 16 health frames, no unavailable
providers and no other PID's network frames. Ingestion recorded no invalid-reader
metadata, confirming removal of the unconditional blank-line gap.

## ETW permission finding

The **unelevated operator could query and stop the disposable SYSTEM-created ETW
session**: ControlTrace query and stop both returned 0. The operator belongs to
Performance Log Users. An agent inheriting that token could interrupt observation;
this is an availability/tamper concern, not SYSTEM code execution. EventAccessQuery
returned access-denied (5), so raw session GUID permissions remain unverified.

No ETW ACL, registry security or group-membership changes were applied. Before
corporate activation, harden and verify session control or worker-token separation.
The user-owned journal remains mutable by its owner; this is not a tamper-resistant
corporate audit ledger.

## Failure cleanup

The cleanup race was closed by stopping the supervisor before enumerating readers,
plus a verification deadline and independent administrator watchdog. Cleanup checks
the run's protected supervisor path/tag, SYSTEM owner, birth, parent, reader path
and test PID. The watchdog also removes the exact temporary task after controller
failure. Neither production task is started or enabled.

A separate no-HTTPS drill terminated the elevated controller with exit 91 after
SYSTEM collection began, bypassing its finally block. The independent 60-second
watchdog stopped/removed the temporary task and reported zero audit sessions.
Final scheduler/CIM checks found both production tasks disabled with zero instances,
zero temporary test tasks and zero native readers. Producer LastRunTime remains
the withdrawn `12:16:51` run; ingestion retains its never-run history.

## Retention selected by the user

| Item | Selected policy |
| --- | --- |
| Searchable journal | 30 days |
| Raw spool | 72 hours after verified ingestion |
| Combined journal/spool/diagnostics | 5 GB (5,000,000,000 bytes) |
| At capacity | Pause and record a gap; preserve un-ingested evidence |

These choices are recorded, **not enforced yet**. No deletion, rotation or capacity
enforcement was enabled. They are sufficient for the initial policy; implementation
and validation remain before activation.

## Evidence and limits

- **47 focused tests passed; one optional grpcio dependency skip.** All ten
  PowerShell scripts parse successfully.
- Post-test access checks: **336 filesystem checks** with positive read controls
  and denied write/delete/ownership/DACL rights, plus **12 task checks**.
- Read-only preflight `2d4c8fd1-01ac-4ff5-8ad9-4cd19111a878` found no blocker to
  bounded testing and identified cleanup/hash guards. Both Git roots and the
  parent's existing-file snapshot were unchanged. The user then approved the
  guards, and their functional/failure paths were exercised above. The tool
  reported no model; model attribution is unknown.
- Summary: `audit-controlled-runtime-evidence.json` beside this file. Full local
  manifests/input inventory/results are in ignored `var/audit-runtime-20261104/`;
  protected spools and task definitions remain under
  `C:\Program Files\BridgeMonitorAudit\verification\<run>` as evidence.

Boot/logon activation, reboot, sustained full-host throughput, ETW control hardening
and retention enforcement were not enabled or tested. Only bounded PID-scoped
checks ran. No existing audit logs or unrelated user work were removed. Both
production tasks remain stopped and disabled.
