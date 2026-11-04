# Controlled build and bounded runtime verification

User authorization: controlled rebuild and ETW/runtime verification, retaining
both `BridgeMonitor-OutboundAudit` and `BridgeMonitor-OutboundAudit-Ingest` stopped
and disabled. Direct Bridge management under the standing audit approval; the
Bridge MCP server remains stopped. No new router or model policy.

Suggested execution model: Claude Opus, advisory, for Windows privilege-boundary
and cleanup reasoning. Execution remains with the user's chosen Codex model.

- [x] Replace inherited build environment/batch shell with explicit toolchain paths,
  complete allowlisted environment, signed tool/support modules and input hashes.
- [x] Compare source to full reviewed commit without clean filters, allowing only
  CRLF/LF differences; compile twice and retain identical output/hash/commands.
- [x] Remove the supervisor's unconditional blank-line gap and add a protected
  test-only PID/spool scope; default production task action remains unchanged.
- [x] Parse the pinned build manifest from its protected copy and validate helper
  ownership before loading it.
- [x] Review the controlled build and bounded SYSTEM test before elevation.
- [x] Run temporary SYSTEM verification tasks, no triggers, 90-second execution
  limit, protected action, owner/DACL AT registration; remove it in finally.
- [x] Exercise two PID-scoped synthetic TLS requests with independent ciphertext
  accounting, native identity/parent/run attribution, forced native crash/restart,
  actual unelevated ETW query/stop rights, and ordinary-user spool ingestion.
- [x] Verify temporary-task/session/process cleanup and both production tasks off;
  record evidence, update topic/index, and save only owned changes.

Retention choices received: searchable journal 30 days; raw spool 72 hours after
verified ingestion; combined budget 5 GB, pausing capture/reporting a gap rather
than discarding un-ingested evidence. These are selected policy values, not yet
implemented deletion/cap enforcement. No deletion is enabled during verification.

The standalone System test lasts roughly 35 seconds. Its controller has a 75-second
deadline and the scheduler a 90-second hard limit. The normal coordinator waits
for privileged cleanup even after a client-side test failure. Temporary tasks are
not the two production tasks; no boot/logon trigger is added. Test scope is one
normal coordinator PID and a protected per-run spool/journal. Synthetic HTTPS uses
the user's previously selected Google destination and persists no response bodies,
headers, packet contents or credentials. ETW ACLs/group membership are inspected,
not changed. The stop-right probe deliberately targets only the disposable second
test session, after its byte comparison. Production activation remains unauthorized.
