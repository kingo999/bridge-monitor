# GPT Architect ⇄ Claude Code (local MCP bridge)

A small Python MCP server that lets **Claude Code call a GPT architect** through the existing **ChatGPT-authenticated Codex CLI**. No separate OpenAI API key is needed. Calls consume your Codex allowance and still count toward its usage limits.

This is a starter integration, not an official OpenAI or Anthropic product. Designed for one local Git repository at a time. GPT is sandboxed read-only; Claude remains responsible for coding and you approve architecture changes.

## Prerequisites (Windows PowerShell)

- Python 3.11+ (`py --version`)
- Claude Code CLI (`claude --version`)
- Codex CLI (`codex --version`)
- ChatGPT Pro and Claude Pro subscriptions

Install Codex CLI if needed:

```powershell
npm install -g @openai/codex
codex login
codex login status
```

**Important:** Select **ChatGPT sign-in**, not an API key, when logging in to Codex. Verify status reports ChatGPT-based auth.

Test standalone Codex first from a repository (replace path):

```powershell
cd C:\dev\fix-planner
codex exec --model gpt-6.1-sol --sandbox read-only "Describe this repo in three sentences. Do not edit anything."
```

If this model is unavailable, select a supported one with `/model` in the Codex UI and set `GPT_ARCHITECT_MODEL` in the bridge's environment.

## Install the bridge

Unzip the package somewhere outside your repository, e.g. `C:\AI\gpt-claude-mcp-bridge`.

```powershell
cd C:\AI\gpt-claude-mcp-bridge
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "mcp"
```

From the **Fix Planner repository root** register the MCP server:

```powershell
cd C:\dev\fix-planner
claude mcp add --scope local --transport stdio gpt-architect -- "C:\AI\gpt-claude-mcp-bridge\.venv\Scripts\python.exe" "C:\AI\gpt-claude-mcp-bridge\architect_bridge.py"
claude mcp list
```

`--scope local` makes this bridge available only in that project (and to your own Claude Code setup). Repeat `claude mcp add` from MRI3's root when you're ready to use it there too.

Inside Claude Code:

```text
/mcp
```

Confirm `gpt-architect` is connected. Then say:

```text
Call consult_architect to review the current Fix Planner architecture,
particularly scan ingestion and target-zone applicability. Summarise GPT's
findings, but don't edit anything yet.
```

Then try:

```text
Make a small Java code change. Once the tests pass, call
review_implementation with a focus on persistence semantics and negative tests.
Show me any disagreements between the two models before making further changes.
```

## How it works

1. Claude invokes the `consult_architect` or `review_implementation` MCP tool.
2. The bridge starts `codex exec` with **read-only sandbox** using your existing Codex login.
3. Codex reads relevant local source; no manual file uploads.
4. The bridge captures Codex's final message and returns it to Claude.
5. The Codex session/thread ID is saved **outside Git** in `~/.gpt-architect-bridge/`, keyed separately by project root. Subsequent calls resume that thread.

Codex persists its own thread history. Also keep approved architecture decisions in Git (`architecture/decisions`, AGENTS.md): conversation history is useful, but not a reliable permanent architecture record.

## Model and effort

Defaults: `gpt-6.1-sol` and `high`. To override, set environment variables *before starting Claude*:

```powershell
$env:GPT_ARCHITECT_MODEL = "gpt-6-astra"
$env:GPT_ARCHITECT_EFFORT = "high"
```

Changing the model starts a new logical bridge session for that model. You can also keep Sol as default and create a second MCP server under a different name with `GPT_ARCHITECT_MODEL=gpt-6-astra` passed through `claude mcp add --env`.

## Caveats

- Claude Code and Codex run under **different subscription allowances**; this bridge doesn't merge accounts or give GPT access to your ordinary ChatGPT conversation history.
- Codex sessions consume ChatGPT Pro **Codex usage**, but there are no metered OpenAI API charges **provided your Codex CLI is signed in with ChatGPT**. Other authentication modes may bill differently.
- The bridge resumes persisted Codex sessions. If Codex is upgraded and a session can't be resumed, an error is returned instead of silently treating it as the same architect.
- Read-only sandbox prevents Codex from editing project files; it does not prevent data from being transmitted to OpenAI. For company code, confirm Broadcom's approval for use with these personal accounts.
- This prototype is single-user and assumes one active Claude Code session per project. For concurrent sessions, add a cross-process lock and structured audit log.
- Start with Sol to conserve your Pro Codex allowance; switch to Astra only for difficult architectural reviews.
- If Claude cannot see the CLI executable, make sure `codex` is on the PATH inherited by Claude's MCP server.

## Remove the bridge

```powershell
cd C:\dev\fix-planner
claude mcp remove gpt-architect --scope local
```

Removing the MCP registration does not delete your saved Codex history or the small thread-ID files under `~/.gpt-architect-bridge/`.
