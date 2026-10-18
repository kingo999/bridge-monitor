# Bridge Monitor pilot runbook

This runbook operates only `C:\AI\bridge-monitor`. The existing bridge in
`C:\AI\gpt-claude-mcp-bridge-v2` remains unchanged. A side-by-side
`bridge_monitor` Codex registration is available for the controlled Skynet
agent pilot; the prior `gpt-architect` registrations remain in place. An isolated
CLI pilot can still use a temporary parent MCP configuration.

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
- The selected exchange can show recent captured model output before a result
  completes. `config\dashboard-service.example.toml` controls the preview's
  chunk and byte limits and the timeline's early/recent event counts under
  `[dashboard]`. `work_item_max_limit` sets the authenticated history query
  ceiling (100–2,000 rows); the ordinary first page remains 100 rows, and an
  expanded list uses the configured ceiling on refresh before falling back to
  further bounded pages. A dashboard service restart picks up these values.
  The preview is a bounded tail; exact complete bodies remain in the retained content
  store and load through the operator-only content endpoint when available.
- Inspect the active reviewed policy with `scripts\policy-control.py` and
  validate that the requested `sonnet` and pinned Qwen model/digest match the
  installed capabilities. A soft reset activates only settings permitted by
  the policy controller; restart-required changes need a process restart and
  ownership reconciliation before new dispatch. The pilot model and concurrency
  values live in config and active policy, not in dashboard code.

## Start and inspect

On Dave's Windows host, two per-user Task Scheduler entries now own the
project-local PostgreSQL cluster and dashboard. Their editable, nonsecret
settings are in `config\windows-tasks.example.json`; both actions point to
scripts inside this project. The PostgreSQL task runs the server while signed
in, and the dashboard task waits for database readiness, reads its credentials
from the existing ACL-restricted `var\secrets` files, and launches the static
dashboard. The dashboard wrapper restarts its server after the configured
delay if it exits, and both tasks have a three-attempt Task Scheduler restart
setting for wrapper failures. Both tasks start at sign-in.
Codex restarts do not own these processes. Inspect with `Get-ScheduledTask
-TaskName BridgeMonitor-Postgres,BridgeMonitor-Dashboard` and
`Get-ScheduledTaskInfo -TaskName <name>`. Start each with
`Start-ScheduledTask -TaskName <name>` if needed. Task actions contain paths,
not tokens or DSNs. `scripts\install-monitor-tasks.ps1` previews task names and
paths by default; `-Install` registers them once and refuses to overwrite an
existing task.

The following manual start commands remain useful for recovery if the scheduled
tasks are disabled. Do not run them alongside active tasks on the same ports.

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
3. For an isolated CLI pilot, generate the temporary parent MCP file with
   `scripts\write-parent-mcp-config.py` and point only that invocation at it.
   For the registered path, `scripts\start-desktop-mcp.py` reads the ignored,
   ACL-restricted `var\secrets\desktop-mcp.json` and uses
   `config\runtime-capabilities.proposal.toml`. When the active policy selects
   the Skynet agent, the launcher reads its token from the project-local
   `agent_token_file` named in those protected settings and passes it under the
   policy's environment reference. Starting the parent MCP replays
   its durable telemetry spool and resumes queued paid recovery calls; it does
   not replay an uncertain inference.
4. Watch the dashboard's capture state, live workers, queue counts, held
   uncertain capacity, routed/not-routed counts, paid fallbacks and dispositions.
   Drill into exact prompt and response bytes only with an operator session.

For a parent `delegate_claude` call that may ask Claude to delegate once to
Skynet, set the task packet's `call_budget` to at least two: one reservation is
for Claude and one for its child. A budget of one leaves no child call and
records a paid route with reason `root_budget_exhausted`; it does not test
Skynet availability. The [18 October diagnostic](../verification/code-reasoning-pilot-2026-10-18.md)
records both cases.

## Stop, restart, and uncertainty

Run `scripts\stop-monitor.ps1` to stop only the dashboard PID recorded by
`start-monitor.ps1`. It verifies the project Python path and original process
start ticks before sending a stop, records the outcome in
`var\run\dashboard-last-stop.json`, and leaves PostgreSQL and model executions
untouched. A running dashboard task will restart it after an unexpected exit;
disable that task first when a persistent stop is intended. If the recorded
process is absent or mismatched, inspect the receipt;
do not stop a reused PID. Stop the PostgreSQL cluster separately with `pg_ctl
stop -D C:\AI\bridge-monitor\var\postgres -m fast -w` only after capture and
model activity have been reviewed; disable its scheduled task first to prevent
an automatic restart. A held uncertain slot is not proof that its
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
DSN. The backup directory must have protected Windows ACL inheritance and
grant access only to the operator account, SYSTEM and Administrators. The
backup command checks this boundary, including any output subdirectory,
before writing retained prompts and responses; it fails closed if the ACL is
broader. Audit existing backup files separately after any ACL repair. Use a
protected PostgreSQL passfile for password authentication. To check
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

## Request-scoped Skynet status pilot

The reviewed route is in `config\pilot-policy.skynet-agent-proposal.toml` and
is active as version `445c3debabdc63c8` (source hash
`445c3debabdc63c876c61ace504b0d1f281f8f52b95cb2b8fbee80ee5e428629`).
The filename retains `proposal` for traceability. The approved policy uses the
agent endpoint and a 120-second Qwen execution deadline. The former direct
Ollama policy is retained in protected pre-activation backups for rollback.
The new route submits one idempotent job per child call ID, polls its output,
and uses bridge-local first-output, idle-output and total deadlines. A healthy
agent response never extends those deadlines. On timeout the Claude owner gets
the existing paid-continuation signal; the uncertain Qwen slot remains held.

The Skynet agent release is built with `scripts\package-skynet-agent.py` and
copied to `dave`'s `~/.local/share/bridge-monitor-agent/releases/`. The
installed pilot points `current` at release `20261017-status-r5`, with an isolated
venv and a user service named `bridge-monitor-skynet-agent.service`. The
service reads `~/.config/bridge-monitor-agent/agent.toml` and a mode-600
`token.env`. The bearer token is referenced as an environment variable; do
not put it in policy or journal files. The agent listens on `192.168.0.78:8874`
and also checks the bridge host's `192.168.0.24` client IP. Update the allowlist
if that address changes. The `dave` user now has `Linger=yes`, so the user
service is configured to survive logout; host sleep can still interrupt work.
Check `systemctl --user is-active
bridge-monitor-skynet-agent.service` and authenticated `/v1/health` after either
event. Agent health verifies the model digest but is not per-job progress.

Use `scripts\probe-skynet-agent.py --policy
config\pilot-policy.skynet-agent-proposal.toml --capabilities
config\runtime-capabilities.proposal.toml --max-seconds 60
--max-output-tokens 32` for a bounded model-only smoke check after setting
`BRIDGE_MONITOR_SKYNET_TOKEN` from the protected local token file. It creates
one job and prints its ID, result and event counts without changing active
routing. The HTTP status receipt should show the same job ID, contiguous
sequence, `completed`, and the pinned model. PostgreSQL prompt/response capture
is verified separately through the synthetic integration test. The agent's
SQLite journal contains hashes and terminal metadata only; output deltas live
in a bounded memory buffer and may be lost on restart, in which case status
reports a gap or `unknown`.

The approval reference is `call_639f7ad0d5704dd6895e989a7a36e48a`.
Startup activation used `scripts\activate-pilot-policy.py` after confirming no
busy jobs or source locks and taking a restricted policy and database backup.
The desktop launcher was restarted and a model-free stdio handshake exposed
the four expected parent tools. A registered Codex CLI exchange and a separate
native desktop exchange each completed GPT → Sonnet → Qwen with an `accepted`
Claude disposition, `local_retained` resolution and no held capacity. Their
audit receipts are under `var\probes\registered-agent-pilot-20261017.audit.json`
and `var\probes\native-agent-pilot-20261017.audit.json`. To stop the pilot service, run
`systemctl --user stop
bridge-monitor-skynet-agent.service` on Skynet. Stop admitting new Qwen work
first and preserve any uncertain receipts and held capacity for reconciliation;
do not free a slot merely because the agent stopped. Restore the backed-up
direct policy and restart the bridge only after checking active calls.
