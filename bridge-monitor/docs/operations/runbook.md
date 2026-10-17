# Bridge Monitor pilot runbook

This runbook operates only `C:\AI\bridge-monitor`. The existing bridge in
`C:\AI\gpt-claude-mcp-bridge-v2` and Codex's normal client registration stay
unchanged. Use a temporary parent MCP configuration for a controlled pilot.

## Preconditions and secrets

- Use the project-local `.venv\Scripts\python.exe`, PostgreSQL data directory
  `var\postgres`, and static dashboard export `dashboard\out`. Run
  `scripts\verify-paths.ps1` before launch.
- Keep `BRIDGE_MONITOR_DSN`, `BRIDGE_MONITOR_OPERATOR_TOKEN`,
  `BRIDGE_MONITOR_SYSTEM_TOKEN`, and `BRIDGE_MONITOR_PRODUCER_TOKENS_JSON` in the
  process environment or a protected secret source. Do not put credentials in
  TOML, command receipts, Git, or browser storage. PostgreSQL password material
  should use a protected passfile, rather than an inline DSN password.
- The dashboard is operator-only. It exposes retained prompts and responses on
  demand through `/api/v1/content/{content_id}`. Its default listener is
  loopback; a non-loopback listener requires configured TLS. Restrict database
  and backup access to the same operator boundary.
- Inspect the active reviewed policy with `scripts\policy-control.py` and
  validate that the requested `sonnet` and pinned Qwen model/digest match the
  installed capabilities. A soft reset activates only settings permitted by
  the policy controller; restart-required changes need a process restart and
  ownership reconciliation before new dispatch. The pilot model and concurrency
  values live in config and active policy, not in dashboard code.

## Start and inspect

1. Start the project-local PostgreSQL cluster with `pg_ctl start -D
   C:\AI\bridge-monitor\var\postgres -l
   C:\AI\bridge-monitor\var\logs\postgres.log -o "-h 127.0.0.1 -p
   55481" -w`. Verify the DSN names the intended pilot database.
2. Run `scripts\start-monitor.ps1 -ConfigPath
   C:\AI\bridge-monitor\config\dashboard-service.example.toml`. It accepts
   only a config inside the project, launches the dashboard hidden, and writes
   `var\run\dashboard-process.json` with PID, executable and start identity.
   Verify `/api/v1/session` responds on the configured listener and inspect
   `var\logs\dashboard.stderr.log` if it does not.
3. Generate the temporary parent MCP file with
   `scripts\write-parent-mcp-config.py`. Point only the pilot client invocation
   at that file. Starting the parent MCP replays its durable telemetry spool and
   resumes queued paid recovery calls; it does not replay an uncertain inference.
4. Watch the dashboard's capture state, live workers, queue counts, held
   uncertain capacity, routed/not-routed counts, paid fallbacks and dispositions.
   Drill into exact prompt and response bytes only with an operator session.

## Stop, restart, and uncertainty

Run `scripts\stop-monitor.ps1` to stop only the dashboard PID recorded by
`start-monitor.ps1`. It verifies the project Python path and original process
start ticks before sending a stop, records the outcome in
`var\run\dashboard-last-stop.json`, and leaves PostgreSQL and model executions
untouched. If the recorded process is absent or mismatched, inspect the receipt;
do not stop a reused PID. Stop the PostgreSQL cluster separately with `pg_ctl
stop -D C:\AI\bridge-monitor\var\postgres -m fast -w` only after capture and
model activity have been reviewed. A held uncertain slot is not proof that its
remote request stopped.

For a pilot client shutdown, stop admitting work, let completed calls drain,
and request `cancel_work` for calls still owned by that client. Record each
terminal or uncertain outcome before closing the temporary MCP client. The
dashboard stop command only stops the web service; it cannot cancel a separate
client's model process. Do not stop PostgreSQL while a recovery decision is in
flight.

After a parent MCP restart, queued paid replacements resume from durable links.
An active or uncertain call does not re-dispatch. Use
`scripts\reconcile-execution.py --call-id <id> --evidence-file <receipt.json>
--actor-id <operator>` only with a correlated process-exit/adapter termination
receipt or live evidence. A stale heartbeat or Qwen resident-model list never
proves a request has ended. Confirmed termination can schedule one paid recovery
for each orphaned local child. If capacity or budget rejects it, the dashboard
must show that state; do not silently mark the work resolved.

Cancellation requests reach queued descendants and active children. Queued
reservations are refunded. Active local capacity remains held until a response
or correlated termination is recorded. If the CLI process tree cannot be killed
under the operator's Windows rights, leave the slot uncertain and investigate
the process. Never force-clear a held slot just to resume throughput.

## Capture outage and retention

The SQLite spool is in `var\spool`. During a collector outage it retains event
IDs and exact body bytes up to the configured size. With the default
`reject_new_work` admission policy, pressure or a known gap blocks new work.
On reconnect, replay the spool; replay sends events and content only and never
starts a model call. A gap remains visible and cannot be reconstructed.

Preview configured expiry with `.venv\Scripts\python.exe
scripts\apply-retention.py --actor-id <operator>`. Back up first, then pass
`--apply` to remove eligible old body bytes and publication entries. The action
retains identity, hash and byte length as a `removed` tombstone, and records
the new replay boundary in `retention_runs`. Registered guidance bytes,
approved example bodies, unresolved-disposition results and prompts for
nonterminal runtime jobs are held. An expired cursor prompts an
SSE reset with a current snapshot; content access returns unavailable after
retention. Replaying an old event cannot restore removed bytes.

## Backup, restore, and rollback

Run `scripts\backup-monitor.ps1 -DsnEnvName BRIDGE_MONITOR_DSN` while the project
database is available. It writes a custom-format dump and SHA-256 receipt under
`var\backups`, refusing any outside-project output or inline password in the
DSN. Use a protected PostgreSQL passfile for password authentication. To check
a restore, create
a fresh database named `bridge_monitor_test_restore_*` in this project cluster
and run `pg_restore --no-owner --no-acl --dbname=<separate-test-DSN>
<backup.dump>`. Compare migrations, calls, results, content, events and runtime
state counts before accepting a backup. Never restore over the active pilot
database during a check. A backup older than a retention run can restore
expired body bytes; run retention again on the restored instance before any
operator or browser access, then check the new replay boundary.

Rollback of the controlled pilot means stop the temporary pilot client
invocation and continue using the existing bridge registration. Stop the
dashboard separately if desired. Preserve the project database, spools, logs,
receipts and uncertain execution records for analysis. Broader registration or
routing changes are outside this runbook.
