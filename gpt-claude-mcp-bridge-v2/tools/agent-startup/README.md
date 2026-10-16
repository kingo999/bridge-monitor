# Agent stack startup (START-01)

One command after a reboot or login: start or reuse the Codex desktop app and the OBS-01
dashboard, then run bounded, model-free health checks on the registered MCP bridges and the
CLI sign-in status. Python standard library only; no installs, no login attempts, no model
calls, no registration or configuration changes.

## Start

```powershell
powershell -NoProfile -File C:\AI\gpt-claude-mcp-bridge-v2\tools\agent-startup\start-agent-stack.ps1
```

It works from any current directory and is idempotent: repeating it reuses what is
already running. If your execution policy blocks local scripts, that is your decision to
change; this wrapper does not alter it.

| Parameter | Meaning |
|---|---|
| `-HealthOnly` | Launch nothing (no app, no dashboard). Report only. |
| `-SkipAppLaunch` | Do not start the Codex app; still start/reuse the dashboard and check. |
| `-DashboardPort N` | Loopback dashboard port (default `8089`). Use an unused port for isolated trials. |
| `-PythonPath P` | Python 3.11+ (default: the `claude-implementer` registration's interpreter). |
| `-AppExecutable P` | Explicit app executable instead of the installed `OpenAI.Codex` package. |
| `-ConfigPath P` | Codex config to read, never written (default `<checkout>\.codex\config.toml`). |
| `-StateDir D` | Bridge state directory handed to the dashboard. |
| `-TimeoutSeconds N` | Seconds allowed per distinct bridge launch (default 45). |
| `-HelperWaitSeconds N` | Overall bound on the health helper (default `TimeoutSeconds + 60`). |
| `-SkipCliAuth` | Skip the CLI sign-in status commands. For isolated fixture runs only; normal startup checks sign-in. |

Exit code: `0` all checks passed, `1` one or more checks failed, `2` prerequisites missing
(Python, helper, config) or the helper did not complete. A pass is `pass_native_unverified`,
never "ready".

## Use from an IntelliJ terminal

In the IntelliJ terminal the Codex desktop app is not what you want launched, so skip it:

```powershell
powershell -NoProfile -File C:\AI\gpt-claude-mcp-bridge-v2\tools\agent-startup\start-agent-stack.ps1 -SkipAppLaunch
```

This starts/reuses the dashboard, probes the bridges and checks CLI sign-in, which is what
Codex or Claude Code running in that terminal needs. It does not say anything about
IntelliJ's own AI Chat: whether the IDE's native MCP client lists or reaches these bridges
is **unverified** by this tool, and no registration is created or changed here. Direct
transport health is evidence about the bridges, not about any particular client.

## What it does

1. **Codex app.** The package is found dynamically (`Get-AppxPackage OpenAI.Codex`) and its
   manifest application whose executable is `app/ChatGPT.exe` is used. Only processes whose
   path is exactly that executable count as the app; other ChatGPT-named processes are
   ignored. If it is running it is reused; otherwise it is launched through
   `shell:AppsFolder\<PackageFamilyName>!<ApplicationId>`. It is never restarted.
   The launcher cannot select a checkout, so the report asks you to open the trusted
   checkout in Codex yourself.
2. **Dashboard.** Port `8089` (or `-DashboardPort`) is checked with bounded GETs under a hard
   wall-clock deadline (a dripping server cannot stall it): root HTML, both assets, and a
   valid `obs-01/1` snapshot. Only an identified, healthy OBS-01 is reused. A free port gets
   the dashboard started by `stack_health.py dashboard-start`, which spawns `dashboard.py`
   directly with stdin/stdout on `NUL`, stderr in `.runtime\dashboard.err.log`, no inherited
   handles and no console window, so it keeps serving after the script exits and never pins
   the pipes of whatever captured the script's output. A port held by anything else is
   refused and its owner is never touched.
3. **MCP transport.** For `gpt-architect`, `gpt-architect-v1`, `gpt-architect-v2` and
   `claude-implementer`, a fresh temporary stdio client starts the configured
   command/args/env from the checkout, runs `initialize` and `tools/list`, and expects
   `consult_architect` + `review_implementation` (GPT) or the five Claude tools. For
   `claude-implementer` it also calls the read-only `capture_source_state` and
   `list_receipts`. Registrations with an identical launch are probed once and every
   alias is reported. Receipt recovery inside `list_receipts` follows FLOW-01 semantics
   and never relaunches work. `consult_architect`, `review_implementation`,
   `implement_changes` and every other tool are never called (enforced by an allow-list).
4. **CLI sign-in.** `claude auth status --json` and `codex login status` run as bounded,
   output-limited subprocesses (stdin closed, 64 KiB cap, timeout) and are classified only as
   `signed_in`, `not_signed_in`, `unknown` or `error`. Nothing is logged in, nothing is
   modified, and no CLI output, account detail or error text is kept or shown.
   `signed_in` requires a zero exit status; a nonzero exit is never reported as signed in.
   Every attempted check that does not establish `signed_in` (`not_signed_in`, `unknown`,
   timeout, nonzero exit, missing executable, oversized or unparseable output) is a failed
   check with fixed metadata `cli_auth:<name>:<code>` and a nonzero exit; sign-in is then
   also listed under "not verified". Only the explicit fixture option `-SkipCliAuth` reports
   `skipped` as an unverified warning instead.

Persistent stdio bridges remain owned by the Codex and Claude clients; this wrapper starts
no detached stdio servers.

### Process ownership and cleanup

Every temporary child (bridge probe or CLI status command) is created suspended, assigned
to a Windows Job Object with `KILL_ON_JOB_CLOSE` (assignment failure aborts the check), and
only then resumed. After the check the whole job is terminated and verified empty, including
descendants left behind after the root exited by itself. Because the job dies with its
handle, forcibly killing the helper also reaps its temporary trees. Cleanup outcomes:
`exited`, `descendants_reaped`, `killed`, `unreaped` (a failed check, reported as
`cleanup_failed`). A server that exits with a nonzero status after a clean protocol run is
`nonzero_exit`. On non-Windows the probe's own process group is tracked and killed instead.
Existing bridges and other processes are never signalled; only these owned children are.
Each probe also has its own watchdog: shortly after the probe deadline it kills the owned
tree, which releases a write blocked on a server that stopped reading (`write_timeout`), and
the tree is killed before stdin is closed so no flush can block. This bounds a direct helper
run without the outer wrapper. When the server exits but a descendant keeps stdout open, a
missing response is reported promptly as `exited_early`.
The wrapper bounds the helper with a hard deadline and stops the helper's process tree if it
expires.

## Reading the report

- **MCP transport pass** means a fresh direct client could initialize the server and see the
  expected tools. It does **not** show that the Codex app's own tool connection is live:
  that is always reported `unverified`. Re-open or refresh tools in Codex to confirm.
- **Dashboard healthy** is server liveness. **Telemetry incomplete** (with reason codes such
  as `no_telemetry`) is a separate warning about the data, not a failure of the server.
- Only fixed codes and tool names are reported, never configuration, environment values,
  paths, server or CLI output, or raw exceptions. Failures look like `mcp:<name>:<code>`
  or `cli_auth:<name>:<status>`; codes include `startup_timeout`, `exited_early`,
  `initialize_failed`, `tools_missing`, `tool_call_failed`, `oversized_output`,
  `nonzero_exit`, `write_timeout`, `cleanup_failed`, `command_missing`,
  `registration_missing`. A clean cleanup is `exited` or `descendants_reaped` (a launcher
  may leave its real interpreter for the job to reap); both mean nothing was left running.

The human report is followed by `--- JSON summary ---` and a JSON document; the same JSON is
kept in `.runtime\last-report.json`. `.runtime\` is ignored and holds no secrets.

## Start at Windows login (optional, not installed)

Nothing here registers autostart. If you want it, create a shortcut in your Startup folder
(or a Task Scheduler task "at log on" for your user) yourself, with:

```
powershell.exe -NoProfile -WindowStyle Hidden -File "C:\AI\gpt-claude-mcp-bridge-v2\tools\agent-startup\start-agent-stack.ps1"
```

## Tests

```powershell
C:/AI/gpt-claude-mcp-bridge/.venv/Scripts/python.exe -B -I C:/AI/gpt-claude-mcp-bridge-v2/tools/agent-startup/test_stack_health.py
```

Fixture-only: a throwaway stdio MCP server, fake CLIs, the real dashboard over an empty
state directory, and loopback ports nobody uses. Regressions covered include early parent
exit with a live descendant, forced helper death, nonzero exit and failed cleanup, dripped
HTTP headers/body, bounded CLI auth results with secret suppression, the dashboard started
from a caller that captures output pipes, and a wrapper timeout. The PowerShell and
dashboard tests start only this repository's `dashboard.py` on a free port and stop it by
verified command line; they skip when PowerShell or the execution policy is unavailable.
Fixture runs use `-SkipCliAuth` / `--cli-auth skip` (or fake CLIs through the fixture-only
`--claude-argv` / `--codex-argv` options) so no real CLI is invoked.

## Limits

- Cold-reboot behaviour, the real native Codex or IntelliJ AI Chat tool connection and live
  bridges are not exercised by the fixture tests; they need an independent live run.
- CLI sign-in classification depends on the status commands' current output (JSON
  `loggedIn` for Claude; "logged in"/"not logged in" text for Codex). A change in wording
  shows up as `unknown` (a failed check), never as a false `signed_in`.
- If the shell that runs the script kills its children on exit through a job that forbids
  breakaway, a dashboard started from it can end with it; the helper tries to break away
  and otherwise stays in that job.
- The dashboard's request log (`.runtime\dashboard.err.log`) is truncated at each start and
  grows only with requests.

## Repository selection

These tools live beside the bridge, outside application repositories. Run the
launcher from the intended checkout, or pass `-Root D:/IdeaProjects/mri/mri-java-services`.
The Python health helper likewise defaults to the caller directory and accepts
`--root`. The launcher reads that checkout's `.codex/config.toml`.
