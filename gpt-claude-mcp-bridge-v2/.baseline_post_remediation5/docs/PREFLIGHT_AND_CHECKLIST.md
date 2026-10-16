> **HISTORICAL (pre-remediation).** The CLI facts in §3 are source observations that remain
> true for 0.162.1; the requirement→test table in §6 and the 'no blocker' conclusion in §5
> are superseded by `docs/REMEDIATION_CHECKLIST.md` and `REVIEW_PACKAGE.md`.

# v2 preflight findings and implementation checklist

Recorded **before** implementation, 2026-10-12. Everything below was gathered with
read-only local commands; no model call was made and nothing was logged in/out.

## 1. Recorded versions

| Item | Value | Source |
|---|---|---|
| Python (v2 venv) | 3.13.7 (`py -3.13`; matches v1) | `python --version` |
| MCP SDK | `mcp==1.30.0` | `pip freeze` in the clean v2 venv |
| anyio / pydantic / starlette | 4.15.1 / 2.14.1 / 1.7.0 | same |
| Codex CLI | `codex-cli 0.162.1` | `codex --version` |
| Node | v22.23.1 | `node --version` |
| OS | Windows 11 Pro 10.0.26300 | |
| v1 env drift | v1 venv also has `pydantic 2.14.0`, `httpcore2`, `httpx2`, `mcp-types`, `opentelemetry-api`, `truststore`; a clean `mcp==1.30.0` install does not. v2 is tested against the clean set. | `pip freeze` diff |

## 2. v1 contract (captured as fixtures)

* `tests/fixtures/v1_tool_schemas.json` – exactly two tools, one required string
  argument each (`question`, `focus`), titles `consult_architectArguments` /
  `review_implementationArguments`.
* `tests/fixtures/v1_subprocess_behavior.json` – argv/kwargs for fresh and resumed
  calls, produced by importing v1 with `subprocess.run` mocked.

Observed v1 behaviour that v2 must preserve or deliberately change:

| v1 behaviour | v2 decision |
|---|---|
| fresh: `exec --json --sandbox read-only --model M -c model_reasoning_effort=E --output-last-message F -` | keep, add explicit `sandbox_mode`/`approval_policy` config |
| resume: `exec resume ID --json -c sandbox_mode=read-only -c effort --output-last-message F -` (**no `--model`**, no approval policy) | add `--model` and approval policy on resume |
| prompt via stdin, argv array, no shell | keep |
| thread ID stored as `{thread_id, model}` in `~/.gpt-architect-bridge/<sha256(root)[:24]>.json`, non-atomic write | replaced by versioned registry in a **separate** v2 state dir |
| `threading.Lock` only | cross-process file locks |
| sync tool functions (block the MCP event loop for the whole run) | async tools, worker thread, cancellable |
| any `error` event => failure | ordered semantics, see §4 |
| no telemetry | JSONL telemetry |

## 3. CLI facts verified from the installed binary (not assumed)

* `codex exec` has `--sandbox`, `--json`, `--output-last-message`, `--model`, `-c`,
  `--disable <feature>`, `--ignore-user-config`, `--ignore-rules`, `--ephemeral`,
  `--dangerously-bypass-*` (never used). **It has no `--ask-for-approval`**; the
  approval policy must go through `-c approval_policy="never"` (`never` is a value
  listed in the top-level `-a` help).
* `codex exec resume` has **no `--sandbox`** flag; it does accept `-c`, `--model`,
  `--json`, `-o`, `--disable`. Sandbox therefore has to be forced via
  `-c sandbox_mode="read-only"` on resume (v1 did this too).
* `codex login status` prints `Logged in using ChatGPT` and exits 0 on this machine;
  it does not print credentials. No `login`/`logout` is ever invoked by v2.
* `CODEX_API_KEY` (and `OPENAI_API_KEY`) are read by the binary. They are an
  API-billing route, so the child environment has them removed (see README).
* JSONL event type names present in the binary: `thread.started`, `turn.started`,
  `turn.completed`, `turn.failed`, `item.started`, `item.updated`, `item.completed`,
  `error`. Usage field names present: `input_tokens`, `cached_input_tokens`,
  `output_tokens`, `reasoning_output_tokens`, `total_tokens`
  (**exact nesting of `usage` on `turn.completed` is a fixture-based assumption**
  until the opt-in live smoke test confirms it; parser treats absence as `null`).
* `codex debug models` returns the local model catalog (~0.8 s, offline). It lists
  `gpt-6.1-sol` with reasoning levels `low, medium, high, xhigh, max, ultra`, so
  model and effort can be validated before inference.
* `codex mcp list --json` honours `-c` / `--disable`, so the *effective* MCP set can
  be verified without a model call:
  * `--disable plugins` removes plugin-sourced servers (`cua_repl`, `code-review`,
    `codex_app`) from the list;
  * config-defined servers (`idea`, `node_repl`) need
    `-c mcp_servers.<name>.enabled=false`;
  * `-c mcp_servers.<plugin_server>.enabled=false` for a server that has no config
    entry is a hard error ("failed to load bootstrap configuration");
  * `--disable <unknown_feature>` is a hard error, so flags must be intersected with
    `codex features list`.
* `codex.cmd` is a shim around `node .../@openai/codex/bin/codex.js`. Python's
  `shutil.which("codex")` resolves to `codex.CMD`. v2 runs `node codex.js` directly when
  it can find it (avoids `cmd.exe` argument re-parsing), else falls back to the shim.

## 4. Security audit result for this machine (affects v1 too)

`~/.codex/config.toml` enables: MCP servers `idea` (JetBrains IDE – exposes file
edit/refactor tools) and `node_repl`; plugins `browser`, `computer-use`,
`unified-computer-use`, `codex-app-tools`, `code-review`, document/pdf/spreadsheet
tools; `[windows] sandbox = "elevated"`; global `~/.codex/AGENTS.md` and
`rules/default.rules`. No hook definitions were found at user level.

**Consequence:** the read-only *sandbox* constrains shell/apply_patch only. A v1 call
can reach file-mutating tools through the `idea` MCP server or computer-use/browser
plugins; the sandbox does not apply to them. v2 disables these routes per call and
*verifies* the effective MCP set through `codex mcp list --json` before inference.
Hooks and feature-level routes (apps/browser/computer use) are disabled with
`--disable` but **cannot be verified without a live call**; that is covered by the
opt-in smoke test (§ README "Live smoke test").

## 5. Is a material architecture change required?

No blocker. One **behaviour change for human review**: v2 disables, per call, every
Codex MCP server/plugin/hook/app not explicitly allowed
(`GPT_ARCHITECT_MCP_ALLOW`). GPT will no longer see the IDE MCP tools that v1 exposed
implicitly. `--ignore-user-config` was *rejected* as the mechanism because it would
also stop loading `[windows] sandbox = "elevated"` from `config.toml`; what the Windows
sandbox does without that setting cannot be confirmed without a live call, so the
bridge keeps the user's config and disables capabilities explicitly instead.

## 6. Requirement → implementation → verification checklist

| # | Requirement | Implementation | Verified by |
|---|---|---|---|
| 1 | Auth: status check w/o reading credentials; require ChatGPT; no login/logout/fallback | `codexcli.check_auth` runs `login status`, classifies output, scrubs API-key env | `test_auth.py` |
| 2 | Read-only on every call; no bypass flags; MCP/hook audit; argv arrays | `codexcli.build_argv`, `capability_audit` | `test_argv.py`, `test_audit.py`, smoke test (live, proposed) |
| 3 | Versioned registry, atomic writes, locks, per-repo isolation, admin cmds | `state.py`, `admin.py` | `test_state.py`, `test_admin.py` |
| 4 | Model/effort routing, per-tool overrides, validation, no fallback | `config.py` | `test_config.py` |
| 5 | Telemetry JSONL, null usage, redaction, one terminal record | `telemetry.py` | `test_telemetry.py` |
| 6 | Error categories, bounded capture, tree kill, no replay, strict completion | `errors.py`, `runner.py` | `test_runner.py` |
| 7 | Pinned deps, CLI compat, install/rollback docs | `requirements.lock.txt`, `README.md` | `tools/verify_lock.py` |
| – | Exactly two tools, unchanged schemas, stdout hygiene | `server.py` | `test_server.py` (incl. real stdio client) |
