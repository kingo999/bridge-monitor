# Separate Skynet local-model adapter

- Status: Proposed
- Date: 2026-10-15
- Decision authority: pending
- Revision examined: 4f2216210097664e0ee9ad852d3532267ffc50f4

## Context

Source observation: the existing GPT bridge uses ChatGPT-authenticated Codex
CLI. Claude owns implementation under the operating agreement. Kian requested
a separate adapter and explicit routing to the LAN-only Ollama host, preserving
the GPT bridge, registrations and accepted ADRs.

## Options considered

Architectural assessment: a separate native Ollama adapter preserves existing
contracts and exposes explicit per-request context limits. Repointing Codex
provider configuration would expand configuration/authentication scope. A general
provider dispatcher would introduce forwarding and approval propagation early.

## Decision

Proposed: implement LOCAL-01 as specified in
[the written design](../../superpowers/specs/2026-10-15-local-01-skynet-adapter-design.md).
Route only explicitly selected bounded local summarization, extraction and
drafting to the fixed Skynet endpoint/model. Do not infer authority from model
output or execute tools. Keep all existing registrations unchanged; expose the
new adapter through a CLI and a directly launched stdio MCP server.

## Consequences

Architectural assessment: separate state, fixed budgets, explicit routes and no
fallback keep provider identity and failures visible. Native Codex/IntelliJ
sessions do not automatically discover this unregistered server. Startup and
dashboard integration remain outside LOCAL-01. Rollback removes only the new
component when idle; it does not modify existing bridge or Ubuntu configuration.

## Acceptance criteria

Offline contract/transport/persistence tests, independent review, Windows health
and synthetic native inference verification, receipt inspection, and preservation
checks in the design must pass before LOCAL-01 is described as verified.

## Evidence

Source observation: see the base-revision references and dated evidence in the
written design. The 2026-10-14 Windows discovery succeeded; the 2026-10-15 refresh
timed out, so current live reachability is unverified. This Proposed record
claims no implementation, model-quality guarantee or acceptance.

## Exceptions

None. Approval, publication and implementation verification are distinct.
