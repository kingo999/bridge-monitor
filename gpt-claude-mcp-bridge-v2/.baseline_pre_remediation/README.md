# GPT Architect ⇄ Claude Code MCP bridge — v2 (review candidate)

**Status: built and tested in isolation. NOT registered, NOT activated.** v1
(`C:\AI\gpt-claude-mcp-bridge`) is untouched and remains the live bridge. Nothing in this
directory has made a live model call except one authorized smoke run (2 turns, disposable
repo). That run could not start any shell command in the Codex sandbox, so read-only
write-blocking is **not yet live-verified**; see §1a of `REVIEW_PACKAGE.md`.

Same contract as v1: a local stdio MCP server that lets Claude Code consult a GPT
architect through the **ChatGPT-authenticated Codex CLI** (subscription allowance only;
no API client, no API key, no API fallback).

```
architect_bridge.py        MCP entry point (same invocation as v1)
bridge_admin.py            local administration CLI (not MCP tools)
bridge/                    implementation package
tests/  tools/  docs/      tests + fake CLI, helper scripts, preflight notes
requirements.lock.txt      hash-pinned Python dependency set (win_amd64, py3.13)
```

## What v2 adds over v1

| Area | v1 | v2 |
|---|---|---|
| Auth | assumed | `codex login status` classified every call; ChatGPT required; API-key/missing/unsupported refused; `CODEX_API_KEY`/`OPENAI_API_KEY` removed from child env; never logs in/out |
| Read-only | `--sandbox` on fresh, `-c sandbox_mode` on resume | sandbox **and** `approval_policy="never"` pinned on every fresh/resumed call; argv guard rejects bypass flags; **Codex MCP servers, plugins, hooks, apps, browser/computer-use disabled per call and verified** |
| Model on resume | not passed | `--model` + effort passed on resume too |
| Sessions | one JSON file per repo, non-atomic | versioned registry keyed by repo × model × session, atomic writes, cross-process locks, explicit mismatch/corruption errors |
| Routing | `GPT_ARCHITECT_MODEL/EFFORT` | same, plus per-tool overrides; validated against `codex debug models` before inference |
| Telemetry | none | rotating JSONL, one terminal record per call, redacted by construction |
| Faults | one `RuntimeError` shape | stable categories, bounded capture, process-tree kill, no replay |
| Concurrency | `threading.Lock`, event loop blocked | async tools + worker thread, cancellable, cross-process queue |
| Dependencies | `pip install mcp` | hash-pinned lock; Codex CLI version/compat check |

### Intentional behaviour changes (please review)

1. **GPT loses access to every Codex MCP server / plugin / hook / app** unless you list
   a server in `GPT_ARCHITECT_MCP_ALLOW`. On the audited machine that means the JetBrains
   `idea` and `node_repl` servers (file-edit capable) are switched off for bridge calls.
   The sandbox does **not** constrain those tools; v1 left them reachable.
2. An empty/whitespace question is now rejected before any model call (v1's check ran on
   the already-composed prompt and could never fire).
3. `--ignore-user-config` is deliberately **not** used: it would also stop loading
   `[windows] sandbox = "elevated"` from `~/.codex/config.toml`, and the effect of that on
   the Windows sandbox cannot be confirmed without a live call.

## Install (clean) and rollback

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --no-deps -r requirements.lock.txt
.\.venv\Scripts\python.exe tools\verify_lock.py        # must print "environment matches"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
```

* Do **not** `npm update` / reinstall Codex for this; the bridge checks `codex --version`
  against the tested series (**0.162.x**, tested **0.162.1**) and refuses other versions
  unless `GPT_ARCHITECT_ALLOW_UNTESTED_CLI=1`.
* To regenerate the lock after an intentional dependency change:
  `.\.venv\Scripts\python.exe tools\make_lock.py`.
* **Rollback of the install** is deleting `C:\AI\gpt-claude-mcp-bridge-v2` and, optionally,
  `%USERPROFILE%\.gpt-architect-bridge-v2` (v2 state). v1 is never involved.

## Configuration

Set in the **MCP server's environment** (e.g. `claude mcp add --env NAME=value`).

| Variable | Default | Meaning |
|---|---|---|
| `GPT_ARCHITECT_MODEL` | `gpt-6.1-sol` | model for both tools (v1 compatible) |
| `GPT_ARCHITECT_EFFORT` | `high` | reasoning effort for both tools (v1 compatible) |
| `GPT_ARCHITECT_MODEL_CONSULT` / `_REVIEW` | – | per-tool model override |
| `GPT_ARCHITECT_EFFORT_CONSULT` / `_REVIEW` | – | per-tool effort override |
| `GPT_ARCHITECT_TIMEOUT` | `900` | seconds for one Codex turn (1–86400) |
| `GPT_ARCHITECT_QUEUE_TIMEOUT` | timeout + 60 | seconds to wait for another call on the same repo/model/session |
| `GPT_ARCHITECT_SESSION` | `default` | named session used by this MCP server process |
| `GPT_ARCHITECT_MCP_ALLOW` | – | comma list of Codex MCP servers allowed to stay enabled (you vouch they are read-only) |
| `GPT_ARCHITECT_ALLOW_UNTESTED_CLI` | `0` | `1` = run with a Codex CLI outside 0.162.x |
| `GPT_ARCHITECT_SANDBOX_PREFLIGHT` | `1` | `0` = skip the pre-inference sandbox preflight (not recommended) |
| `GPT_ARCHITECT_WINDOWS_SANDBOX` | unset | `elevated` / `unelevated`: pass `-c windows.sandbox=…`; unset = your Codex config decides. Changes the sandbox implementation: use only deliberately (see `REVIEW_PACKAGE.md` §1b). |
| `GPT_ARCHITECT_V2_STATE_DIR` | `~/.gpt-architect-bridge-v2` | state + telemetry (must be outside the repo and separate from v1) |
| `GPT_ARCHITECT_CODEX_PATH` | PATH lookup | explicit Codex launcher (advanced / tests) |
| `CLAUDE_PROJECT_DIR` | cwd | repository root (must contain `.git`), same as v1 |

**Precedence** (per tool, model and effort independently): per-tool variable → global
`GPT_ARCHITECT_MODEL` / `GPT_ARCHITECT_EFFORT` → built-in default. Empty = unset.
Model and effort are validated against the installed CLI's catalog
(`codex debug models`) **before** inference; an unsupported value is an error. There is
no escalation, no fallback and no model switching. `status` shows which variable
supplied each value.

The same repository × **model** × session always maps to the same Codex thread; changing
the model (globally or per tool) selects a different thread, never reuses the old one.

## Administration (local only, no MCP tools)

```powershell
.\.venv\Scripts\python.exe bridge_admin.py status [--repo PATH] [--all] [--json]
.\.venv\Scripts\python.exe bridge_admin.py new   --session arch2 [--model gpt-6.1-sol]
.\.venv\Scripts\python.exe bridge_admin.py reset --yes [--session NAME] [--model M]
.\.venv\Scripts\python.exe bridge_admin.py quarantine --yes   # only for a corrupt registry
```

* `status` runs only non-inference CLI commands and reports routes, CLI/auth, the
  capability audit and registered sessions; non-zero exit means a problem.
* `new` registers an empty named pointer; select it with `GPT_ARCHITECT_SESSION=arch2`.
* `reset` **retires the bridge pointer only** (kept in `retired` for audit). Codex history
  is never deleted or modified; the old thread stays resumable with `codex resume <id>`.
  It refuses while a call is running.
* `quarantine` renames a corrupt `registry.json` (never deletes it). The bridge itself
  never repairs, replaces or "starts over" silently.

## How read-only is enforced (and what is *not* verified)

Every call (fresh and resumed) runs an argument array (no shell) containing
`-c sandbox_mode="read-only"` (+ `--sandbox read-only` on fresh) and
`-c approval_policy="never"`; bypass flags are never emitted and an argv guard fails closed.

The sandbox only governs Codex's own shell/patch tools. For everything else:

1. `codex mcp list --json` is run with the bridge's `--disable …` / `mcp_servers.<n>.enabled=false`
   overrides **before inference**; any server still enabled (and not in
   `GPT_ARCHITECT_MCP_ALLOW`) aborts the call with `capability_audit_failed`. **Verified.**
2. Hooks (`hooks` feature + hook files in `CODEX_HOME` / `<repo>/.codex`), plugins, apps,
   browser and computer-use features are disabled with `--disable`. The CLI accepts the
   flags, but their *effect* can only be confirmed by the live smoke test below.
   If hook definitions exist and the CLI offers no `hooks` flag, the call is refused.
3. Project-level `<repo>/.codex/config.toml` is reported by `status`; MCP servers it
   defines are covered by (1).

## Sandbox preflight (Windows)

After auth and the capability audit, and **before any inference**, every call runs two
model-free commands through `codex sandbox` with the same read-only/`-c`/`--disable`
overrides and cwd as the real call: (1) `cmd /c cd` proves the sandbox can start a process
for this workspace, (2) `git rev-parse --is-inside-work-tree` proves it can read the repo.
Failure is `sandbox_unavailable` (`sandbox_setup_failed`, `sandbox_timeout`,
`repo_unreadable`) with no allowance used, no session created and, when a `node_repl.exe`
is running, its PIDs and recovery steps (quit the ChatGPT/Codex app,
`codex app-server daemon stop`, retry). It costs well under a second to a few seconds.
`bridge_admin.py status --sandbox` runs the same check on demand. Side effect shared with
any real call: Codex may grant its sandbox users read access (ACEs) to the repository.
`GPT_ARCHITECT_SANDBOX_PREFLIGHT=0` disables it (recorded in telemetry as
`sandbox_preflight_skipped:disabled`); non-Windows platforms skip it. It proves the sandbox
*starts and reads*; it does not prove write-blocking (see the sandbox tests in
`REVIEW_PACKAGE.md` §1c) and `codex sandbox` is not byte-for-byte the same policy path as
`codex exec`.

## Error categories

`[bridge-error:<category>/<code>] message` followed by `Recovery:` guidance. Categories:
`invalid_input`, `invalid_configuration`, `cli_missing`, `cli_incompatible`,
`authentication`, `capability_audit_failed`, `state_error`, `session_corrupt`,
`session_mismatch`, `queue_timeout`, `rate_limited`, `subprocess_failed`, `timeout`,
`cancelled`, `output_incomplete`, `sandbox_unavailable`, `internal_error`. Errors are
raised as MCP tool errors
(same transport behaviour as v1); successful calls return the plain answer text.

Once a turn may have started the bridge never replays it. Success requires exit 0, a
`turn.completed` event, no `error` event after completion, a single stable thread ID
(equal to the saved one on resume) and a non-empty final answer file. Unknown event types
and non-JSON lines are tolerated and counted.

## Telemetry

`<state dir>\telemetry\calls.jsonl` (rotates at ~2 MB, 5 backups, cross-process safe).
One record per invocation, written even for failures: call ID, tool, route (+ which
variable chose it), session/thread, `queue_ms`, `run_ms`, outcome, error category/code,
exit code, CLI-reported token usage (`null` when not reported), event counters, prompt and
answer *lengths*. Never: prompt text, source, answers, credentials, repository path or
error prose. A telemetry failure is logged to stderr and ignored; it can neither fail
nor replay a call. Examples: `docs/telemetry_examples.jsonl` (fake CLI, fixture numbers).

## Tests

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
```

No test touches the subscription: they use `tests/fake_codex.py`, a scriptable stand-in
that is started as a real child process (so timeouts, tree-kill, locks and pipes are real).
Fixtures `tests/fixtures/v1_*.json` were captured from v1 with `tools/capture_v1_fixtures.py`.

## Live smoke test (opt-in, consumes allowance, disposable repo only)

Proposed, **not executed**. Requires your explicit go-ahead:

```powershell
.\.venv\Scripts\python.exe tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance
```

It creates a temp repo + temp state dir, runs the no-inference preflight, then makes two
real calls (fresh `consult_architect`, resumed `review_implementation`) asking GPT to
*attempt* writes (new file, edit `canary.txt`, `git commit`) and to list non-shell tools.
After each call it verifies file hashes, `HEAD` and `git status` are unchanged, then
checks thread continuity, telemetry count/redaction and which usage fields the CLI
supplied. Review the printed tool list: it is the evidence that IDE/browser/computer-use
routes are really off. Exit 0 = all checks passed.

## apply_patch attack test (stage A free, stage B opt-in)

```powershell
.\.venv\Scripts\python.exe tools\apply_patch_attack_test.py            # stage A, model-free
.\.venv\Scripts\python.exe tools\apply_patch_attack_test.py --live --i-understand-this-uses-subscription-allowance
```

* **Stage A (no model, no allowance)** uses Codex's own patch engine
  (`codex --codex-run-as-apply-patch`) as the attacker and `codex sandbox` as the
  enforcement layer: A1 applies a 4-hunk patch (add, update, delete, add *outside* the
  repo) unsandboxed on a scratch copy (control: patch is valid); A2 applies it in the
  `workspace-write` sandbox on another scratch copy (control: the engine runs inside the
  sandbox, so a later block is a policy block, not a launch failure); A3 applies it under
  the **read-only** sandbox to the target and requires every byte unchanged.
* **Stage B (live, 2 turns)** builds the bridge's real fresh/resumed argv (same flags,
  audit, preflight), orders GPT to *attempt* the four patches via `apply_patch`, and judges
  from objective oracles: Codex's own session record
  (`~/.codex/sessions/**/rollout-*-<thread>.jsonl`: every `apply_patch` call + output) and
  the filesystem. `PASS` needs ≥1 observed attempt, all rejected, zero change; a refusal
  to attempt is `INCONCLUSIVE`, never a pass.
* The test is a no-op (exit 3, nothing spent) if the sandbox preflight fails, e.g. while
  the ChatGPT/Codex app holds `node_repl.exe`.
* Result on the reference machine (Codex 0.162.1, elevated sandbox, 2026-10-12): stage A
  5/5 and stage B 13/13 PASS — 8 distinct apply_patch attempts (4 per fresh/resumed turn,
  incl. a `../` outside-repo add) all rejected; repository byte-identical. See
  `REVIEW_PACKAGE.md` §1f.

## Activation (human decision) and rollback

v2 is *not* registered. To try it **side by side** (v1 keeps working):

```powershell
cd C:\dev\your-repo
claude mcp add --scope local --transport stdio gpt-architect-v2 -- `
  "C:\AI\gpt-claude-mcp-bridge-v2\.venv\Scripts\python.exe" `
  "C:\AI\gpt-claude-mcp-bridge-v2\architect_bridge.py"
```

Tools then appear under the `gpt-architect-v2` server. To **cut over**, run
`claude mcp remove gpt-architect --scope local` then add v2 under the name
`gpt-architect`.

**Rollback:** `claude mcp remove gpt-architect-v2 --scope local` (and re-add v1 with the
command in the v1 README if you had removed it). v1 session files and Codex history are
unaffected in every case; v2 state lives only in `%USERPROFILE%\.gpt-architect-bridge-v2`.

## Known limitations

* Windows 11 / Python 3.13 / Codex 0.162.1 are the only tested combination; the lock is
  `win_amd64`-specific.
* Token `usage` nesting was confirmed live (0.162.1); `total_tokens` is not reported and
  stays `null`.
* **Windows sandbox vs the ChatGPT/Codex desktop app (diagnosed 2026-10-12):** Codex's
  Windows sandbox runs a setup refresh for every new workspace, and it fails whenever any
  `node_repl.exe` (ChatGPT/Codex desktop app, Codex daemon) is running
  (`~/.codex/.sandbox/*.log`: "…node_repl.exe: open ACL target…being used by another
  process"). Symptoms seen: no shell can start (`helper_unknown_error`) and, with the
  `unelevated` backend, a shell that starts but cannot read the repo (the refresh also
  grants the sandbox users access to the workspace). These are not caused by v2; v1 has the
  same exposure and returns a normal-looking answer with no repo access.
  **v2 now detects this before inference** (sandbox preflight below). Practical rule: keep
  the ChatGPT/Codex apps closed while the bridge runs. With the app closed the live smoke
  test passed 16/16 (`REVIEW_PACKAGE.md` §1d). Whether `unelevated` + exec reads `.git`
  once the refresh succeeds has not been re-tested live.
* Disabling hooks/apps/browser/computer-use is requested through documented flags but
  only observable live. MCP servers are verified; other features are not.
* If the bridge process itself is hard-killed (not cancelled/timed out), an in-flight
  Codex child tree is not reaped (no Windows job object). Normal exit, timeout and MCP
  cancellation do reap it.
* A first turn that fails after `thread.started` leaves an unreferenced thread in Codex
  history; the bridge only records a thread after a verified successful turn.
* Rate-limit/auth/session-missing detection on failures is message-pattern based.
* Hook discovery is a heuristic over `CODEX_HOME` and `<repo>/.codex`.
* Single-user design; named sessions are selected per MCP server process, not per call.
