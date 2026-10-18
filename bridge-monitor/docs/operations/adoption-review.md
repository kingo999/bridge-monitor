# Bridge Monitor adoption review — 17 October 2026

Status: **side-by-side Skynet agent pilot active and validated through native
Codex desktop and the installed Codex CLI registration**. The existing Codex registrations and both
older bridge source trees remain unchanged; `gpt-architect` remains registered.
The approved policy is `445c3debabdc63c8` with a 120-second Qwen deadline.
Both registered GPT → Sonnet → Qwen exchanges ended `accepted` and
`local_retained`, with zero held slots. The native desktop tool appeared in a
fresh validation chat after its MCP server became ready. The
[activation and exchange audit](../verification/skynet-task-status.md#activation-and-registered-client-pilot)
contains the exact boundaries.

## Earlier paid-only validation and incident history

- The earlier active project policy in `bridge_monitor_pilot_m5_20261017` was version
  `77f6331062bd9666`, source hash
  `77f6331062bd9666e22bd354421240c676bfe07381883f525301459378903eb6`.
  It selects Claude `sonnet` and the pinned
  `qwen2.5-coder:14b-instruct-q4_K_M` profile. Its Qwen execution deadline is
  **1 second**, retained from the timeout test policy. This was an absolute
  execution-stage cutoff, not a one-minute inactivity threshold. The active
  agent pilot now uses 120 seconds.
- Qwen call `dbf268d3-6bd3-462b-b676-2a7174e096c0` timed out at 13:14:42
  Prague time and took its already completed paid fallback. Skynet's Ollama
  journal later established one matching `/api/generate` request from bridge
  host `192.168.0.24` in the bounded window. At 13:14:42.830 it logged that
  the client connection had closed before model load finished and aborted the
  load; the request ended with HTTP 499. This server-side record, unlike an
  empty `/api/ps` list or a sleep/wake report, proves the individual request
  terminated. The [journal excerpt](../../var/probes/qwen-termination-skynet-20261017.journal.txt)
  and [structured receipt](../../var/probes/qwen-termination-skynet-20261017.evidence.json)
  retain the timestamps, host boot ID, journal cursors and bridge identity.
- Authenticated operator reconciliation at 20:23 Prague time set that call to
  `terminated` and `slot_held=false`. The database has one matching termination
  reconciliation, zero held Qwen or Claude slots, and no new paid recovery.
  The original fallback transition remains `completed` with its paid result.
  A [pre-reconciliation database backup](../../var/backups/pre-qwen-reconciliation-20261017T182221Z.dump)
  was taken first (SHA-256
  `C33F59365E2563CB836007A7469F54C2BE3B0EBD65DF4306D225FEB4DEFF3CD6`).
  A [post-reconciliation backup](../../var/backups/post-qwen-reconciliation-20261017T182401Z.dump)
  preserves the audit row and released capacity (SHA-256
  `7A05609E7801BE0F6887CC6A323A6A8402A694235EDCA3EEBDEAA31E6577B9DC`).
- Skynet was temporarily unreachable while asleep, then returned on
  `192.168.0.78`. The configured Qwen digest still matches. The user enabled
  SSH on the LAN and installed a project-local public key for the `dave`
  account, allowing read-only host journal inspection. No new inference was
  sent during this investigation.
- The running Codex process has no Bridge Monitor credentials in its
  environment. The new project-local launcher reads its DSN from the ignored,
  ACL-restricted `var/secrets/desktop-mcp.json` and generates an ephemeral
  system token. The Codex TOML contains neither credential.
- Codex now lists `bridge_monitor` alongside `gpt-architect`,
  `gpt-architect-v1` and `claude-implementer-internal`. The registered launcher
  passed a no-inference stdio handshake and exposed exactly `cancel_work`,
  `complete_work`, `delegate_claude` and `get_execution_status`.

## Installed side-by-side registration

The following entry was added to the user's Codex configuration after a backup
at `var/backups/codex-config-before-bridge-monitor-20261017T1834Z.toml`. The
backup is ACL-restricted to the operator account and SYSTEM. The
runtime capability proposal initially set the paid-client timeout evidence
flag to `false`, so local Qwen readiness could not pass on the basis of the CLI
pilot alone. Later model-free desktop probes are described below.

```toml
[mcp_servers.bridge_monitor]
command = 'C:\AI\bridge-monitor\.venv\Scripts\python.exe'
args = ['C:\AI\bridge-monitor\scripts\start-desktop-mcp.py']
tool_timeout_sec = 600

[mcp_servers.bridge_monitor.tools.delegate_claude]
approval_mode = 'approve'
```

An isolated Codex CLI run using the normal user configuration explicitly called
`bridge_monitor.delegate_claude` for one read-only arithmetic task. Its call
completed through requested `sonnet`, reported `claude-sonnet-5-5`, with one
`paid` decision (`owner_model_incompatible`), no Qwen job and zero held slots.
The exact tool exchange is retained in
`var/probes/desktop-paid-only-20261017.jsonl`, and the database audit in
`var/probes/desktop-paid-only-20261017.receipt.json`. The returned answer was
captured, but the completion claim was not valid, so `accounting_complete` is
false and no work resolution was recorded. Do not count GPT's acceptance in its
chat text as a bridge disposition. The diagnostic was `fallback transition not
bound to this owner`: Sonnet had filled `fallback_child_call_id` from a
not-routed child call that had no fallback transition.

The worker instruction now distinguishes a paid continuation with an actual
`fallback_state` from a pre-dispatch paid route. Repeating the same bounded
task produced one `paid` decision, requested `sonnet`, reported
`claude-sonnet-5-5`, a `paid_direct` work resolution, no completion diagnostics,
`accounting_complete=true`, no Qwen job and zero held slots. The repeat
[receipt](../../var/probes/desktop-paid-only-after-guidance-20261017.audit.json)
and [tool exchange](../../var/probes/desktop-paid-only-after-guidance-20261017.jsonl)
retain the evidence. The focused Claude adapter suite passed 9 tests.

The local dashboard is running at `http://127.0.0.1:8873/` from the static
Next.js export. Its operator login credential is in the ignored,
ACL-restricted `var/secrets/dashboard.json`. A loopback page request and an
authenticated session check both returned HTTP 200. The database and dashboard
are still running. Authenticated work-item detail and metrics requests for the
new probe also returned HTTP 200. `scripts/stop-monitor.ps1` stops only the
dashboard.

On a later Codex turn, sandboxed process and `pg_ctl status` checks reported
both services absent even though the dashboard still owned loopback port 8873
and the project PostgreSQL process was running. An attempted stack restart
found that port already bound; no replacement service was installed. Its stale
receipt cleanup removed the dashboard PID files, which were restored only after
matching the live process command lines, start ticks and port owner. Check
service status from a context that can see the running processes and confirm
loopback reachability before restarting either service.

The original task and its fresh same-directory fork did not expose the tool.
Desktop startup logs at 20:50:56 Prague time show that the fork loaded the
registration but its MCP handshake timed out after 30 seconds. Direct TCP and
HTTP checks then confirmed that the project PostgreSQL and dashboard listeners
were unavailable; a standalone handshake also timed out after 20 seconds.
This is a startup failure, rather than evidence that the registration was
ignored. After restarting the project services, the standalone handshake
completed in 1.022 seconds and exposed all four parent tools. The dashboard
returned HTTP 200, the pilot database was reachable, and zero slots were held.
The stale dashboard process receipt was preserved before replacement. The
[retry receipt](../../var/probes/desktop-startup-retry-20261017.receipt.json)
records these checks without credentials. No new inference was sent, and no
user configuration or runtime capability setting was changed during this retry.

After service recovery, a further fresh desktop task exposed all four tools.
A native `bridge_monitor.delegate_claude` call completed in 12.093 seconds:
requested `sonnet`, reported `claude-sonnet-5-5`, and answered `7 * 8 = 56`.
Its attempted local delegation was recorded as `paid` with
`owner_model_incompatible`, preserving the existing not-routed definition.
There was one completed Claude job, no Qwen job, one `paid_direct` resolution,
`accounting_complete=true`, no completion diagnostic, and zero held slots.
The bridge status reported healthy capture with zero known gaps or pending
events. PostgreSQL read-only queries independently verified the exact submitted
prompt and delivered answer bytes against their SHA-256 hashes, and retained
the complete raw worker response. Events included 18 response deltas, the
tool exchange, model selection/reporting, usage and result delivery. The native
[tool exchange](../../var/probes/desktop-native-paid-only-20261017.exchange.json)
and [database audit](../../var/probes/desktop-native-paid-only-20261017.audit.json)
retain the evidence. This confirmed the desktop tool path for that paid-only
check. The later agent policy activation required a client restart.

The configured parent timeout is 600 seconds, but the 12-second paid call did
not test a long deadline. The restricted worker has no harmless wait or shell
tool, so a separate temporary, model-free MCP server used the same
`tool_timeout_sec = 600` setting and waited 75.000 seconds. Its single native
desktop tool call completed after 75.003 seconds of tool execution; the task
measured 79.065 seconds end to end. This establishes that a desktop MCP call
can survive beyond the documented 60-second default, but does not exercise the
full 600-second deadline or Bridge Monitor delegation for that duration. The
temporary registration and script were removed, and the user Codex config was
restored byte-for-byte to its pre-probe SHA-256
`25E01F9C0578DB490117B54E147BA3AF19B0AE37CA36728F46C92BB416367011`.
The [diagnostic receipt](../../var/probes/desktop-timeout-probe-20261017.receipt.json)
records the task and MCP call identities. The desktop runtime capability flag
remained `false` at that point. Do not reuse the CLI-only verified file for
desktop admission.

A later temporary model-free native desktop tool with the same
`tool_timeout_sec = 600` configuration returned after a 590.0-second server
wait and 593.074 seconds of measured tool wall time, with the expected marker
and no tool error. Its [receipt](../../var/probes/desktop-timeout-590s-20261017.receipt.json)
and registration state are retained under `var/probes`. The diagnostic MCP
registration was removed after the call. Codex had concurrently changed one
unrelated `node_repl` environment value during the restart; restoration
preserved that app edit and removed only the exact diagnostic registration.
`config/runtime-capabilities.proposal.toml` records the desktop Claude client
timeout as verified. The 590-second probe used no model inference. The held
call was reconciled before activation. Skynet health and the pinned digest were
rechecked, and the registered-client exchange was compared with stored
disposition, route and held-capacity records before considering any broader
routing change.

To roll back this side-by-side registration, remove only the new
`mcp_servers.bridge_monitor` entry and restart the Codex client. Continue with
the unchanged `gpt-architect` registration. Preserve the Bridge Monitor
database, spool and receipts for analysis. If a pilot client has active work,
drain or request cancellation through that client first and retain uncertainty
until a correlated terminal receipt arrives.

The [Milestone 5 verification](../verification/milestone-5.md) and
[pilot runbook](runbook.md) contain the tested boundaries. The user authorized
the side-by-side registration with “agree, proceed” on 17 October 2026.
Changing default routing remains a later decision.

The [17 October pilot observation](../verification/pilot-observation-2026-10-17.md)
separates the four exchanges under the activated Skynet-agent policy from the
ten-item historical observed cohort, checks content integrity and records
the residual `pending` classification limitation without changing its formula.
It also flags one model-reported `accepted` judgment that appears to meet the
contract's `revised` definition; the stored disposition was preserved.

The later [disposition quality audit](../verification/disposition-quality-audit-2026-10-17.md)
compares all five observed `accepted` Qwen-to-Claude pairs. The dashboard now
labels A/R as Claude-reported acceptance and says these records are not
independent quality reviews. Neither the metric formula nor the stored
dispositions changed.
