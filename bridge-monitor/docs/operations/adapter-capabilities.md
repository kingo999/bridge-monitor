# Milestone 2 adapter capability record

Status: the isolated project-local Codex CLI pilot completed one normal Codex → Claude → Qwen exchange and one controlled local timeout with automatic Claude continuation. This is a single-worker pilot, not a global client registration or production cutover.

## Skynet pilot model

- Selected profile: `qwen2.5-coder:14b-instruct-q4_K_M`.
- Endpoint observed: `http://192.168.0.78:11434`.
- Digest observed from Ollama `/api/tags` on 2026-10-16: `9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849`.
- Ollama `/api/show` reported a 32,768-token model context; the existing bridge profile uses 16,384 input-context tokens and 2,048 output tokens. The pilot proposal keeps the 2,048 output reservation. The client timeout remains to be validated before live admission.
- A fresh read-only `/api/show` on 2026-10-16 again reported `qwen2.context_length=32768` and a GPT-2-style byte-level tokenizer. For the bridge's prompt-only `/api/generate` request, its fixed template adds role delimiters shorter than the proposed 128-token allowance. The gate conservatively bounds input tokens by UTF-8 prompt bytes plus 128; each byte-level token consumes at least one byte. This is an inference from model metadata and the exact request shape. The live sample consumed 34 prompt tokens for 19 prompt bytes, well below that bound.
- A short live generation requested `num_ctx=16384` and `num_predict=16`, returned `OK` from the selected model in 3.185 seconds with 34 input and 2 output tokens, and `/api/ps` then reported resident `context_length=16384`. This verifies the effective context setting for that sample, not timeout/cancellation behavior.
- A second project-local probe sent the real adapter a 300 ms shared cutoff. It returned `state=uncertain` at 307 ms with `request_may_have_run` and no completed-response event. The receipt is `var/probes/qwen-cutoff.json`. This verifies the client-side cutoff and conservative uncertainty handling for that request; it does not prove the Ollama server stopped inference. Its local reservation must therefore remain held until termination is proved or reconciled.
- The new bridge policy pins the model name and digest. Its metadata preflight checks `/api/tags` before local reservation and again immediately before the one-way launch claim. A changed profile at that boundary commits a pre-dispatch paid route, releases the local reservation and records `not_routed=true`. An Ollama generation response reports the model name, not the digest; metadata alone cannot prove the binary used for an individual generation.

## Claude client

- Installed CLI observed: version `2.1.301`. Help output advertises restricted mode, strict MCP config, stream JSON output, partial messages and tool filtering.
- Pilot paid-worker choice: Claude Sonnet (`sonnet` CLI alias). The isolated live probe reported `claude-sonnet-5-5`; the runtime proposal lists that observed ID, while the general example leaves model IDs unset.
- The [Claude context-window documentation](https://platform.claude.com/docs/en/build-with-claude/context-windows) describes 200k tokens for older Sonnet models and 1M for current Sonnet 4.6/5 variants. The proposal uses 200k as a conservative family floor, not as evidence of this CLI session's effective context.
- The adapter launches a Claude child with `--tools ""` to remove built-in tools, `--allowedTools mcp__bridge_monitor__delegate_local_analysis` for the sole child operation, `--restricted`, and `--strict-mcp-config` pointing at its lease-bound project-local server. Anthropic's [CLI reference](https://code.claude.com/docs/en/cli-reference) distinguishes built-in-tool restriction from MCP permissions: `--tools` does not affect MCP tools. A bounded live Sonnet probe using this command reported exactly that one tool, used it successfully, connected only the dynamic project-local MCP server, and reported no skills or slash commands. Its init frame still listed three installed plugins as metadata; no plugin tools appeared. The raw stream is `var/probes/sonnet-isolation.jsonl`.
- The installed CLI requires `--verbose` when `--print` uses `stream-json`. The first two live bridge attempts exited with code 1 before any model frame because the adapter omitted that flag. Project-local stderr capture exposed the exact error; both exited processes were reconciled from correlated process-exit events without replay. The adapter now supplies `--verbose`, and later live flows completed.
- The child command also sets `disableAllHooks` in CLI settings. Anthropic's [hooks documentation](https://code.claude.com/docs/en/hooks) says this disables user, project and plugin hooks, while managed hooks can only be controlled by managed settings. No common local managed-settings file was found, but server-managed hook state is not proved absent. This is the remaining isolation boundary.
- The adapter records model identity as soon as the stream reports it, which allows a child-readiness check during an active Claude turn. If the stream does not report a model before child delegation, local readiness rejects it.
- `MCP_TOOL_TIMEOUT` and disabled MCP auto-backgrounding are configured for the child process. The isolated Codex CLI pilot uses a 600-second per-server MCP tool timeout and `runtime-capabilities.codex-pilot.toml` marks that specific client configuration verified. The general proposal retains its paid-client capability flag as false; launching from an unconfigured default client must not pass local readiness.

## Codex parent client

- [Official OpenAI Docs](https://learn.chatgpt.com/docs/config-file/config-reference) lists a default 60-second MCP per-tool timeout and the `mcp_servers.<id>.tool_timeout_sec` override. `scripts/run-codex-pilot.py` starts an ephemeral Codex CLI process with `--ignore-user-config`, strict project-local MCP settings, and `tool_timeout_sec=600`; it never registers the bridge in the existing Codex configuration.
- A harmless MCP echo probe succeeded under the override. A later live pilot call lasted 143.777 seconds and returned through the same client configuration, demonstrating operation beyond the default 60 seconds. The full 600-second duration was not empirically exercised.
- A no-inference stdio MCP handshake using the project-local Python runtime exposed exactly `delegate_claude`, `complete_work` and `get_execution_status` to the parent, and exactly `delegate_local_analysis` to the Claude child. This proves server-side audience separation, not the effective tools available to a launched Claude CLI session.

## Project-local development commands

From `C:\AI\bridge-monitor` in PowerShell, with a dedicated PostgreSQL database already running and `BRIDGE_MONITOR_DSN` set to its connection string:

```powershell
.\.venv\Scripts\python.exe -m database.migrate BRIDGE_MONITOR_DSN
.\.venv\Scripts\python.exe scripts\write-parent-mcp-config.py
.\.venv\Scripts\python.exe scripts\activate-pilot-policy.py --policy config\pilot-policy.toml --runtime config\runtime-capabilities.toml --show-hash
.\.venv\Scripts\python.exe scripts\activate-pilot-policy.py --policy config\pilot-policy.toml --runtime config\runtime-capabilities.toml --approved-hash <reviewed-sha256> --operator-id <operator-id>
$env:BRIDGE_MONITOR_RUNTIME_CONFIG = 'C:\AI\bridge-monitor\config\runtime-capabilities.toml'
.\.venv\Scripts\python.exe -m bridge.mcp_server --audience parent
```

The parent client configuration is `C:\AI\bridge-monitor\var\mcp\parent.json`; creating it does not register or switch any existing client. The MCP process uses stdio and stops when that client disconnects; for a manual terminal launch, stop it with Ctrl+C. The commands do not start or stop PostgreSQL. `config/pilot-policy.proposal.toml` and `config/runtime-capabilities.proposal.toml` contain reviewable candidate values, but deliberately retain invalid review provenance and an unverified paid-client deadline. Do not activate a placeholder policy.

## Remaining live preflight

The 600 seconds is the outer parent `delegate_claude` call ceiling, covering at most 30 seconds queued, 540 seconds of Claude execution and 30 seconds of delivery margin. Qwen's normal 120-second stage and the 60-second paid fallback allowance fit inside Claude's execution; they do not each add to 600 seconds. The child bridge derives that outer deadline from the parent's durable admission time, not from later Qwen delegation. A synchronous parent handler waits for its queued call to gain the pilot slot or expires it at the configured deadline. The normal live flow and one-second controlled Qwen stage are recorded in `docs/verification/milestone-2.md`. The timeout's local reservation remains held because client cutoff does not prove Skynet terminated its inference. Process-restart recovery of abandoned queued calls remains open before broader concurrency; managed-hook absence is not established by the tool-list probe.
