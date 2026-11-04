# Privileged audit installation repair — 2026-11-04

**Later checkpoint:** [controlled build and bounded SYSTEM runtime verification](2026-11-04-audit-controlled-runtime.md)
has passed, with both production tasks still stopped/disabled. ETW stop-right
hardening and unimplemented retention remain before activation. Earlier unrun
checks below describe their original repair checkpoint.

User authorization: repair the stopped privileged installation safely; keep the
audit task disabled until reviewed. This repair grants no activation approval.

The previous SYSTEM execution chain was user-writable. Its activation evidence
does not establish a safe installation. The user stopped and disabled it before
repair. The replacement is installed but both tasks remain disabled and stopped.

## Installed boundary

- `C:\Program Files\BridgeMonitorAudit` is administrator-owned and has a protected
  DACL: SYSTEM/Administrators full control, the named operator read/execute only.
  Existing files, update revisions and staging directories have trusted ownership.
- SYSTEM task `BridgeMonitor-OutboundAudit` executes only the protected native
  reader/supervisor, using protected cwd/temp and Windows System32 search paths.
  It appends metadata to a protected spool. Its action has no checkout reference.
- The native reader imports only ADVAPI32.dll, tdh.dll, WS2_32.dll and KERNEL32.dll.
- `BridgeMonitor-OutboundAudit-Ingest` uses the ordinary operator's limited
  interactive token to read the spool and write the existing user journal. All
  Python, policy, attribution and SQLite processing stays below that boundary.
- Both task owners/DACLs allow unelevated-user read, but no execute, edit, delete,
  enable or permission changes. The consumer principal is Limited/Interactive.
  Explicitly elevated administrators retain maintenance access; an already-elevated
  administrator or UAC bypass is outside this boundary.
- Installation used a pinned-hash bootstrap and administrator-owned staging copy,
  then checked copied reader/supervisor hashes. No task start was requested.
  A subsequent authorized UAC elevation installed a locally reproducible native
  build and corrected supervisor, tightened the consumer DACL, tested the
  protected verifier's elevation refusal, and recorded administrator inventory.
- No OS-wide privilege grants, repository ACL changes, payload capture or network
  requests were added. Existing audit logs and user work were preserved.

## Checks actually run

- Initial repair: 27 focused tests and 43 filesystem denials. Added follow-up
  tests first showed five failures and nine passes. Current focused audit/adapter
  selection: **44 passed**. Tests cover torn/corrupt checkpoint recovery, fsync
  failure preserving the prior checkpoint, discard-state recovery, a real
  concurrent writer and interprocess lock, admin refusal before journal creation,
  verifier output/overwrite guards, and native identity resynchronization after
  consumer restart, as well as the existing audit regressions.
- All seven PowerShell scripts parse without errors. Oversized ETW session numbers
  use `TryParse`; no test creates a real session for this edge case.
- `access-evidence.py`: **161 filesystem checks**, with successful reads and
  denied writes/deletes/ownership/DACL changes, plus **12 effective task checks**
  across both descriptors. The harness records exact masks, Win32 results, its
  own hash, configuration hash, and actual token SID/groups/deny-only flags,
  elevation type and integrity SID. It opens existing handles without writing.
- Normal-user verifier: installed supervisor equals its reviewed source; native
  source/build manifest/output hashes match; trusted owners/DACLs and no reparse
  points; exact actions, principals, full XML, triggers, restart/time/battery
  settings, and both task DACLs. Output must be a new JSON file in project `var`.
  Its process view is explicitly partial when command lines are unreadable.
- Protected administrator inventory at 13:37 Prague time: **zero audit processes,
  zero audit ETW sessions**, `logman` exit 0, and no unreadable Python/PowerShell
  command lines. Both tasks were disabled. It also recorded old/new staging
  ownership/DACLs/hashes, and exercised the verifier's elevated-token refusal
  from a hash-checked protected copy before any report write.
- Startup sweep covered registered tasks, services, machine/operator Run and
  RunOnce keys, and common/operator Startup file bytes. Other Bridge matches
  were Dashboard, GemmaClassifier, Postgres and RouterShadow, all Limited user
  tasks. No additional privileged checkout route was found in that scope.
  No unrelated task/service/registry setting was modified; their command bodies
  were omitted from the evidence. This is not a universal persistence audit.

## Build and bootstrap provenance

`build-evidence.py` built the unchanged reviewed `collector.cpp` twice with
MSVC 14.44.35207, `/MT /Brepro` and linker `/Brepro /INCREMENTAL:NO`. Both output
SHA-256 values equal
`F34588F8AA3771A2599198B43447FE674B94FC22238F26AE02005B6540455237`.
The manifest records source SHA-256 and Git blob (matching integrated `783a504`),
source snapshot, exact commands, compiler/linker versions/hashes/valid signatures,
developer-environment hash, build-log hashes and native imports. The source was
hashed before and after both builds. The ETW executable was never run. This is
local reproducibility using one toolchain, not an independent compiler attestation.

The manifest, exe and corrected supervisor were copied and hash-checked in the
protected installation. Prior protected versions were retained under `revisions`.
The full new bootstrap is tracked in `elevated-install-bootstrap.ps1`; its exact
UAC-command text, payload and hashes are retained locally. It executes only
hash-checked administrator-owned copies of four fixed management-script roles.
The atomic security-descriptor creation helper closes a transient ownership race:
bootstrap/update copies, directories and receipts are created with administrator
ownership and restrictive DACLs, before bytes become available. Prior versions
used Copy-Item followed by owner repair; the new helper does not leave a window
where the unelevated configuring account owns an executable or revision directory.
The evidence bundle also includes the original bootstrap text and staging ACLs.
No scheduled task references either setup directory. The protected setup copies
are retained as inert evidence, rather than removed automatically.

Machine receipts: ignored `var/audit-hardening-20261104/atomic-configuration.json`,
`atomic-access.json`, `atomic-installation-result.json`,
`atomic-payload.json`, `atomic-bootstrap-command.ps1`, and
`reproducible-build-v2/build-evidence.json` with both build logs.
The reviewable bundle is `audit-hardening-installation-evidence.json` beside this file.
The protected installation receipt is `C:\Program Files\BridgeMonitorAudit\installation.json`.
Administrator inventory is `C:\Program Files\BridgeMonitorAudit\verification-admin.json`.
The archived old disabled task XML is evidence only; never restore it.

## Review and remaining limits

Enforced read-only review `4a882574-610c-4dc3-904a-812eb220244a` found no reusable
unelevated-user-to-SYSTEM route, but required clearer guidance and stronger
evidence. Both roots and parent source/Git snapshots were unchanged during review.
The user expressly authorized these follow-ups. Final enforced read-only review
`48efeb96-1217-4123-8461-5b521514fd9a` found **no blocker to the disabled repair**
and no reusable unelevated-user-to-SYSTEM route. Both Git roots were unchanged;
the parent independently checked 2,789 existing files with no drift or remaining
read gaps. Neither review tool reported a model; it remains unknown.

Two requested evidence reconciliations were completed after review:

- Fresh-install atomic creation is reviewed statically only. The existing tree
  was updated in place; bootstrap copies, file/revision creation and protected
  receipts used the atomic helper in the real update. Legacy `data`, `scratch`,
  `revisions` and some older backups retain inherited DACLs and the earlier
  informational group field. Their owner is Administrators, effective writes are
  denied to the unelevated operator, and the group field grants no access by
  itself. Owner-normalization loops remain defense in depth for legacy objects;
  new copies receive protected security descriptors at creation.
- Producer LastRunTime `12:16:51` matches the withdrawn activation receipt
  `12:16:51.2452588`, and precedes the first protected install at `12:42:43` and
  later updates. The consumer has the never-run sentinel `1999-11-30`. A fresh
  Task Scheduler query returned the same values. This is retained old task
  history, not evidence of a replacement start.

Additional parent raw-source verification used `git cat-file blob` with full
commit `783a504dd7a3a7f753156e7de389bb9799a46f75`. Compared without clean filters,
the reviewed source and built source differ only in CRLF/LF endings. The manifest
pins the actual compiled raw bytes. This closes the clean-filter ambiguity; it
does not authenticate a build against operator-level tampering before approval.

Review records and reconciliations are retained in ignored
`var/audit-hardening-20261104/followup-security-review.{md,json}`,
`followup-review-after.json`, `history-reconciliation.json` and
`raw-source-binding.json`. The final test log/receipt records **44 passed, one
optional grpcio dependency skip**. No implementation changes were applied after
the final review; these additions report parent verification and remaining limits.

| Initial finding | Disposition |
| --- | --- |
| 1 Elevated writable verifier | Explicit non-elevated guide; elevation refusal tested from protected copy; new-file/var/reparse output guards |
| 2 Build/source binding | Two identical builds, source/tool/log manifest, protected pinned update, reviewed-source and installed-script comparisons |
| 3 Bootstrap visibility | Full tracked bootstrap and command/payload evidence; both staging trees protected and inventoried |
| 4 Evidence gaps | Reviewable probe harness, token/masks/read controls, both task descriptors/full XML/settings, administrator process/ETW snapshot |
| 5 Other startup routes | Bounded read-only sweep described above; no additional privileged checkout route found |
| 6 Threat-model wording | Explicitly protects unelevated tokens; administrator maintenance/UAC bypass excluded |
| 7 Session parse overflow | Safe positive-integer TryParse; invalid values ignored |
| 8 Retention | Concrete archive/reconcile guidance added; no retention policy/deletion configured; remains an activation prerequisite |
| 9 Untested producer launch | Deliberately unrun under the user's no-start requirement; future separately authorized smoke/logon/reboot tests |
| 10 Partial install | Validated named user SID/build inputs; fail-closed partial evidence/revisions retained; recovery requires inspection, no blind rollback |
| 11 Consumer correctness | Fsync/corrupt-state recovery, persisted discard state, real lock/writer/admin/native-resync tests; limit explicitly includes newline |
| 12 Stale context/plan | Historical activation marked withdrawn, current disabled status first, checklist reconciled |

Do not enable either task on the strength of this document. Replacement live
capture, producer crash/restart, logon and reboot remain deliberately unrun.
The final review adds a controlled rebuild as an activation prerequisite: the
current reproducible build inherited its process environment. A future build
must constrain and record compiler/linker environment overrides and dependency
inputs before its output is approved for elevated execution. Two identical
outputs alone do not rule out a consistently poisoned build environment.
Also verify ETW session ACL/control rights (the operator belongs to Performance
Log Users), correct the supervisor's extra blank-line gap, and exercise the full
supervisor/consumer round trip under separately authorized tests. Minor static
follow-ups include parsing the pinned manifest from its protected copy, helper
trust-order cleanup, and broader verifier/permission tests. These are recorded
future work; the final review found none to block the stopped/disabled repair.
Agree retention limits and disk monitoring before unattended activation; no such
policy was selected here. Consumer recovery is at least once, and may duplicate
committed rows across a crash/checkpoint boundary. Cached DNS hints and interface
inventory reflect ingestion time. The user journal remains mutable by its owner;
this repair does not add journal tamper resistance, a full machine security audit,
enforcement, or protection against an elevated administrator.
