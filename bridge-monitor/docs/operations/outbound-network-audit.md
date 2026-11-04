# Observe outbound agent traffic on Windows

The collector observes connections and sends on the local Windows host. Bridge hooks add logical application requests and run identities. The local SQLite journal buffers capture. The production deployment retains its observations and registration, process and policy state in the Bridge PostgreSQL database under the `outbound_audit` schema. Protect the capture directory, including the WAL and related files.

## PostgreSQL retention and recovery

### Audit dashboard

The Codex/Claude overview includes workflow and measured-usage links. Each opens the exact captured overview cohort in the Audit tab. Audit has Executions and Network activity views. Search normally expands the initial recent view to all retained records. An explicitly chosen time restriction stays active. The Match control distinguishes metadata words, exact identifiers/IPs, process/requested-name prefixes and passive DNS aliases.

Queries create session-owned immutable result snapshots, kept for up to 30 minutes. Active result pages survive background overview refreshes. A session can retain 100 active searches. Expiry preserves the visible form so a new search can regenerate results. Full history refers to PostgreSQL retention and disclosed indexing coverage. It includes older records outside the initial recent view.

The dashboard indexes new PostgreSQL observations and affected workflows in bounded background batches. Migration 015 supplies the search and snapshot schema; 016 replaces shared-key invalidations with an append-only queue. Network and execution publication use independent transactions. Failed indexing retries on a later cycle, with pending work and indexed extent shown by the status API.

Last successful sync comes from the importer checkpoint. Last health signal processed is the time the capture consumer handled a health frame. Backlogged or replayed frames can have recent processing times, so the signal does not prove reader liveness or uninterrupted capture. Requested names and passive aliases, recorded policy results, application-reported bytes and observed host-send bytes remain distinct in evidence.

For maintenance, inspect `/api/v1/audit/status` as an authenticated operator. A source ID is either projected as valid metadata or retained as an explicit validation error. The UI never reads or modifies the SQLite buffer. Pausing the dashboard stops its indexer. Its result projections and tokens can be rebuilt from retained source data, with active result tokens expiring during maintenance.

On this installation, `BridgeMonitor-OutboundAudit-PostgresSync` runs under the logged-in operator every minute. It is independent of both host capture tasks. New SQLite rows accumulate during PostgreSQL downtime or logout and are imported on a later successful run. The buffer is append-only in normal operation. Its disk usage still requires monitoring. A separate retention change is needed before pruning or rotating it.

The restricted importer login reads and inserts into the audit tables, updates the sync marker and mutable process/policy state, and uses temporary staging tables. The local PostgreSQL deployment uses loopback trust authentication. Database role grants constrain the importer connection; the PostgreSQL host authentication rules determine which other local clients may claim a role.

Check `Get-ScheduledTaskInfo -TaskName BridgeMonitor-OutboundAudit-PostgresSync` and `SELECT last_id, synced_at FROM outbound_audit.source`. To reconcile all retained fields, from the project root run:

~~~powershell
.\.venv\Scripts\python.exe -m bridge.audit_pg_sync --settings var\secrets\outbound-pg-sync.json --verify-all
~~~

Protected settings name the canonical SQLite journal and a DSN for the ingest role. Migration 014 creates the schema. Each import binds the canonical path and filesystem identity, reads one SQLite WAL snapshot, and commits PostgreSQL rows and its checkpoint together. Full verification compares every source and target value in both directions. Ordinary sync compares the observation tail including its previous checkpoint, all registrations, both policy tables and current process state. Invalid keys, unexpected source types, replacement files and conflicting stored observations fail the transaction. A failed import can be retried after resolving the cause. For a source replacement or truncation, preserve both files and reconcile manually before any rebind.

To pause transfer, disable the PostgresSync task and wait for any running import to exit. Capture continues according to its independent settings. Preserve both databases before considering the destructive SQL rollback. The verified pre-import SQLite backup is kept in the protected project backups directory. To recreate the scheduled registration, use `scripts/install-audit-pg-sync.ps1` under the normal operator account after configuring the ingest role and settings.

## Privileged installation: disabled pending review

**Current update:** controlled rebuild and bounded SYSTEM runtime/failure-cleanup
checks passed on 2026-11-04. See the [runtime verification](../verification/2026-11-04-audit-controlled-runtime.md).
Both production tasks remain stopped and disabled. ETW control hardening and
retention enforcement remain before activation: the unelevated operator could
stop the disposable test session. No ETW rights or group memberships were changed.

The original always-on installation was unsafe: its SYSTEM task executed a
supervisor, Python interpreter and imports from a user-writable checkout. The
operator stopped and disabled it. Do not restore that execution chain.

The replacement separates the privileged native producer from ordinary-user
ingestion. `BridgeMonitor-OutboundAudit` runs only Windows PowerShell and the
native reader in `C:\Program Files\BridgeMonitorAudit`. The directory, supervisor,
reader and existing files are owned by Administrators, with write access only for
SYSTEM and Administrators. The named operator has read/execute access to files
and read-only access to both task definitions. Its working directory, temporary
directory and executable search path are protected. Its imported DLLs are Windows
system DLLs. No Python, repository code, SQLite journal or user configuration is
read by the privileged producer.

The producer appends metadata to the protected `data\metadata.tsv` spool. The
ordinary-user `BridgeMonitor-OutboundAudit-Ingest` task reads it with
`python -m bridge.audit_spool`, using a limited interactive token. It writes the
existing journal `C:\AI\bridge-monitor\var\audit\outbound.sqlite` and checkpoint
`spool-cursor.json` in that journal directory. The journal remains writable by the
operator and Bridge; it is never opened by the SYSTEM producer.

**Both tasks are installed disabled and stopped.** Installation and verification
never start them. The repair does not authorize activation. Obtain security review
and explicit operator approval before enabling either task. The privileged task
cannot be enabled, executed or modified by an unelevated operator token. An
administrator who explicitly elevates through UAC can maintain these files and
tasks; this design does not defend against an already-elevated administrator.

`scripts/outbound-audit/install-protected-reader.ps1` installs only into a fresh
protected destination, requires reproducible-build evidence, checks pinned inputs before and after
copying, rejects reparse-point ancestry, and registers disabled definitions.
Its installation bootstrap first copies the installer into an administrator-owned
staging directory, verifies its pinned hash there, and only then executes it.
Protected directories and files receive their administrator owner and DACL in
the creation operation, including bootstrap copies, revisions and receipts. The
installer never relies on fixing a user-owned executable's permissions afterward.
Windows policy is respected; the task uses process-level RemoteSigned rather than
Bypass. Existing Group Policy takes precedence. Updates require an administrator
to stage and review a new protected build; editing the checkout cannot update the
privileged executable. Do not change permissions on the development checkout to
try to make it a privileged deployment.

The reviewed `elevated-install-bootstrap.ps1` text is passed in the explicitly
UAC-approved encoded command. It copies and hash-checks fixed management scripts
in an administrator-owned staging directory before executing them there.
`update-protected-reader.ps1` accepts updates only with both tasks disabled and
the existing protected files matching their receipt. It preserves prior files
under protected `revisions`, pins the new build and supervisor, then leaves both
task DACLs read-only for the operator. An interrupted installation/update stays
disabled and retains evidence. Inspect the protected receipt, revision and hashes
as administrator before any recovery; never delete or restore a partial tree
blindly, and never restore the archived unsafe task definition.

Build evidence is generated as an ordinary user with
`python scripts/outbound-audit/build-evidence.py --output var\audit-build-NEW`.
It records the installed MSVC toolchain, signed compiler/linker identities, source
snapshot/hash, exact commands/log hashes and two byte-identical outputs. It never
runs the reader. This is local build provenance, not an independent compiler or
complete supply-chain attestation. The installed manifest and source are checked
by the normal-user verifier.

Spool ingestion accepts metadata as data only. It waits for complete lines,
bounds malformed records, and checkpoints after committed batches. Delivery can
replay records after a crash between database commit and checkpoint; deduplicate
when calculating totals across such a boundary. Consumer restart and detected
spool replacement/truncation emit gap evidence. Cached DNS names reflect the
consumer's local cache at ingestion time, not necessarily the historical send.
Checkpoints are flushed with `fsync` before replacement; malformed checkpoints
emit a gap and replay the retained spool instead of failing permanently. The
oversized-record discard state survives restart. The 65,536-byte record limit
includes its newline. Native identity frames restore attribution before later
network frames after a consumer restart; a gap remains explicit during recovery.
Retention is operator-managed for both spool and journal; no automatic deletion
has been added. An administrator must archive the protected spool while both
tasks are stopped, then reconcile the consumer checkpoint. Disk monitoring and
retention limits remain prerequisites for sustained collection.

The user environment and Codex's `mcp_servers.bridge_monitor.env` both persist
`BRIDGE_OUTBOUND_AUDIT_DB` with this journal path. Existing MCP server processes
need to reconnect to receive the environment change. Application hooks can record
while host capture is disabled; that is partial coverage. Policy evaluation stays
audit-only.

The old activation receipts remain in ignored `var/audit-setup-20261104/`; they
do not validate the replacement. Repair receipts, ACL evidence and task definitions
are in `var/audit-hardening-20261104/`. See the
[repair verification](../verification/2026-11-04-audit-installation-hardening.md).
The replacement was exercised in bounded PID-scoped SYSTEM tests, including reader
crash/restart and controller-failure cleanup, without running either production
task. Boot/logon, reboot and sustained full-host capture remain untested.

Inspect from an **ordinary, non-elevated PowerShell** window with
`Get-ScheduledTask -TaskName BridgeMonitor-OutboundAudit,BridgeMonitor-OutboundAudit-Ingest`.
Administrative task changes require separate elevation; never source a checkout
script in an elevated window. Both tasks are already stopped and disabled.

Run the verifier below from that **non-elevated** window. It refuses an elevated
token and refuses existing reports, output outside this project's `var`, and
reparse-point output ancestry. Choose a fresh report filename each time:

~~~powershell
.\scripts\outbound-audit\verify-protected-reader.ps1 -ReportPath .\var\audit-hardening-check.json
~~~

`access-evidence.py --configuration <that-report> --report <new-var-report>`
records the actual unelevated token SID/groups/elevation/integrity, access masks,
successful read controls, denied write/delete/permission changes, and effective
permissions on both task descriptors. Administrator process/ETW and startup-route
inventory is performed only by the hash-verified protected management script,
with other applications' command bodies omitted from its evidence.

The operator selected 30 days for the searchable journal, 72 hours for raw spool
after verified ingestion, and a 5 GB combined budget. At capacity, capture should
pause and report a gap while retaining un-ingested evidence. These values are
recorded; deletion, rotation and capacity enforcement are not implemented/enabled
by these verification tests. Implement and validate that policy before activation.
For an explicitly authorized future archive: stop both tasks, confirm producer
and consumer exit, retain a journal backup and the cursor, then copy the protected
spool to an administrator-owned archive with a recorded hash. Verify whether the
cursor reached that exact file's identity and length before deciding what can be
retired. Preserve undrained archives for separate replay; do not reuse their
offset against a different file. A new spool identity resets ingestion to zero
with a gap marker. Truncation is also reported. Archive/reconcile work does not
automatically re-enable either task. The append-only producer must be stopped
before moving its open spool.

## Start an isolated observation

From the Bridge Monitor project directory, build the reader in an x64 Visual Studio developer prompt with the Windows SDK installed:

~~~cmd
scripts\outbound-audit\build.cmd var\audit-bin
~~~

The following direct `collect` command is a development diagnostic, not an
unattended privileged installation. Never register it as SYSTEM or as an elevated
scheduled task from a writable checkout. Use the protected producer/ordinary-user
consumer split for an unattended installation.

In PowerShell, choose an existing private directory and start collection. The examples use a local Python environment with Bridge Monitor's dependencies installed.

~~~powershell
$db = 'C:\audit-lab\outbound.sqlite'
python -m bridge.audit_cli --db $db collect .\var\audit-bin\bridge-outbound-reader.exe
~~~

Start the collector before launching the test worker. Set the following variable in the environment of the Bridge instance you want to observe. Open a separate terminal for the collector. Stop it with Ctrl+C.

~~~powershell
$env:BRIDGE_OUTBOUND_AUDIT_DB = 'C:\audit-lab\outbound.sqlite'
~~~

Bridge registers its CLI worker process by PID and Windows creation time. The host collector follows process ancestry. To identify another running worker, register its PID before its workload starts:

~~~powershell
python -m bridge.audit_cli --db $db register --pid 1234 --agent research --run run-17
~~~

Mark a persistent helper used by multiple runs as a shared boundary with
`python -m bridge.audit_cli --db $db register --pid 1234 --shared`.
That process and descendants without a closer explicit registration have unknown
run ownership. The marker applies to that exact process instance. Root exit
evidence also ends inherited ownership. Owned rows label direct registration
as `registered` and inferred ancestry as `process_lineage`; ancestry alone
cannot identify which client called an unmarked shared service.

Host mode observes all processes, including unattributed traffic. The HTTP adapters and classifier attach application evidence where instrumented. Other clients can wrap an individual logical RPC with `bridge.audit_hooks.application_call`, supplying a static operation name, endpoint and known category. Operation names and operator labels must contain metadata only. Use `category=None` when unknown. Two RPCs on a shared connection have individual request IDs. Only host send events carry byte counts.

## Evaluate a policy

Save a draft such as the following as `draft.json`. Rules support exact agent, requested hostname, IP, port and transport. Null or omitted fields are unconstrained. Names are case-insensitive, and IP addresses use canonical forms.

~~~json
{
  "default": "deny",
  "rules": [
    {"id": "research-service", "effect": "allow", "agent": "research", "ip": "192.0.2.8", "port": 443, "transport": "tcp"},
    {"id": "blocked-host", "effect": "deny", "hostname": "blocked.example"}
  ]
}
~~~

~~~powershell
python -m bridge.audit_cli --db $db show
python -m bridge.audit_cli --db $db replay draft.json
python -m bridge.audit_cli --db $db policy draft.json
python -m bridge.audit_cli --db $db show --after 50
~~~

`replay` evaluates saved evidence against the specified draft. `policy` selects the draft for new events. Both report the same content-derived version. An explicit deny wins. In the example, an IP match alone may still give *insufficient evidence* because an unknown requested hostname could match the deny rule. A default of `allow` supports a denylist. Neither mode blocks a connection.

## Reading the evidence

- A host socket attempt records the emitting PID and destination. A TCP connect event and a kernel TCP or UDP send have different meanings. Send bytes are measured at the provider, with no claim of remote receipt. Multiple socket records can describe the same call. Use request IDs to count instrumented application calls.
- Local interface addresses (including loopback) are excluded from host records. Requested endpoints from the application layer describe intent; the host observation establishes whether traffic left. Cached DNS names are possible names for the observed IP. They are read from the local resolver cache without generating a lookup and cannot satisfy a requested-hostname rule. A remote machine's onward traffic requires a collector at that boundary.
- Windows provider access determines coverage. Full process-lifecycle and send evidence requires access to the kernel process and network providers. A restricted account may see Winsock attempts only. The reader checks still-live processes and verifies creation times when process events are unavailable, or when a sender or parent predates collection. Processes that ended before their identity could be verified may lack attribution. Reported tracing gaps clear cached identity evidence. The application hooks alone cannot identify every RPC in encrypted or third-party traffic.
- Look for collector heartbeat and gap records in `show`. A stopped or stale heartbeat indicates unknown coverage. Provider denial, decoding failures and reported ETW loss produce gaps. Kernel coverage here is TCP/UDP. Other IP protocols, provider-side calls and events lost before collection are outside this reader's coverage. Large traces may exceed the collector's throughput.
- Keep the query string, path parameters, headers, credentials, prompts and message bodies outside this journal. It does not perform packet capture or TLS interception. Files show host, process, endpoint and time metadata and require appropriate access controls. Stop writers before archiving or deleting a journal. Size and retention are operator-managed.

The collector records malformed reader input as a gap and continues collecting. On abnormal reader exit, the controller attempts to stop that reader's exact ETW session. Shutdown flushes pending evidence.

Transient SQLite locks are retried without stopping capture. Pending observations
and process-end updates each have a 4096-entry bound. Observation overflow records
a gap and dropped-event count when storage recovers. Process-lifetime overflow
disables attribution until restart, including buffered observations. Shutdown
retries persistence for five seconds, then fails visibly if storage remains busy.
Permanent storage errors remain fatal. Operator retention and disk monitoring are
required for sustained collection.

To check ETW installation, run `python scripts/outbound-audit/smoke.py var/audit-bin/bridge-outbound-reader.exe --require-kernel --result var/audit-smoke.json` with the path to your built executable. The smoke test restricts output to its own PID and uses a loopback receiver. It verifies sender identity and compares received bytes with kernel send counts. `--require-kernel` fails when any requested provider is unavailable. Without that switch, attempt-only evidence returns status `partial` and exit code 2. Full success returns 0; failure returns 1.

For an operator-selected plain HTTP test receiver, supply `--host test-host.example --port 8080 --path /health`. This sends a synthetic GET, requires an HTTP status response, and compares its request size with kernel send counts for that socket. The result records whether the peer is off-machine using the local interface inventory. No machine, provider or model name is built in. The test accepts a static path without credentials or query parameters and retains no response headers or body. Choose a reachable receiver you control, then compare its own access log before rollout.
