# Codex bridge handoff

## Using the existing MCP bridge

The bridge is used unchanged. Pass bounded decision briefs and contracts as repository paths and
revisions inside the existing question or focus strings, for example the contract path plus
`base revision <hash>` plus the dirty-file list. This document does not add a session selector,
automatic context reset or transfer, or any new API.

Named bridge sessions are selected per MCP server process, not per call. Starting a fresh task
context therefore means an explicit human/operator choice of which configured process or session to
use; an agent must not assume a per-call selection, reset or transfer exists, and this repository's
documents do not change session registrations or configuration.

Bridge operation sources live under `C:/AI/gpt-claude-mcp-bridge-v2/tools/`.
Repository contracts remain scoped to the target checkout passed to the bridge.
