# Persistent Markdown context

Project: Bridge Monitor, `C:\AI\bridge-monitor` (Git root `C:\AI`).
Purpose: share significant technical decisions between Codex and ChatGPT
conversations without depending on chat history. Last reviewed: 2026-11-01.
Source paths are relative to the project root unless stated otherwise.
Canonical reusable guidance: [project Markdown context](../docs/operations/project-markdown-context.md).

## Decisions

**Approved (2026-11-01):** the user's request established a lightweight Markdown
convention: one index plus one small file per topic. The user replied "yes" to
direct application of the original three-file patch.

**Approved ownership clarification:** context belongs to the repository being
worked on. Each explicitly selected project owns its own `.ai-context/INDEX.md`
and topics. This directory holds Bridge Monitor decisions only; it is never a
fallback, central store or mirror. The user conditionally authorized completing
the ownership corrections and safe permission handling.

**Proposals:** none. Superseded: none.
Use Approved / Proposed (discussion required) / Superseded; writing a note or
implementing an idea is not approval.

## Reasoning and trade-offs

- Notes stay at the selected project root so ownership is clear and nothing
  accumulates in an unrelated repository.
- The host `write_prefixes` entry is only an upper bound; each task grant must be
  narrower. An already valid grant may make multiple updates. If it lacks a
  context path, use a fresh explicitly authorized grant; host edits never widen
  an existing grant. Authorized sessions maintain notes; no automatic writer is
  added.
- Small files are easy to review and share, but need deliberate upkeep and manual
  sharing with ChatGPT. Review dates and Git refs expose staleness; they do not
  prove freshness.
- Separate from [BM-06 pinned context](../docs/specifications/pinned-context.md):
  no loader, synchronization or runtime input. The
  [runbook](../docs/operations/runbook.md),
  [repository execution](../docs/operations/repository-execution.md) and
  [project access guidance](../docs/operations/project-access-and-queue-maintenance.md)
  remain canonical for Bridge behaviour.

## Implementation and verification

State: implemented and verified (approval is separate from this status).

- Host config: only `"AGENTS.md"` and `".ai-context/"` were added to the
  `bridge-monitor` host write prefixes. Other configured roots are untouched.
- Context writes need explicit read-write access, the selected approved root and exact
  Markdown paths; exact `AGENTS.md` only when that file must change. Existing
  permission and routing policies are not widened or changed in code.
- Existing tasks keep their bound grants. A long-lived MCP process keeps its old
  runtime snapshot; a fresh coordinator via the same launcher loads the new host
  config.
- Checks run: 19 new permission fixture tests passed (run
  `6eb8245a-b8a8-4d0f-9554-b40c6930dc4a`). Red before config: 1 failed, 18 passed,
  expected `task write scope exceeds host grant` (run
  `494b3498-f4f4-4a35-88aa-dc628128acde`). Relevant regression run
  `7fb0c508-97b8-4aa2-989c-ff4e59d6c6f1`: 147 passed, 46 skipped, 0 failures
  (45 DB DSN skips, 1 symlink privilege skip). No full-suite claim.
- Read-only inventory of 8 stored packets: 6 identical grant bindings, 2 still
  denied, 0 authority changes or unknowns before/after the candidate.
- Documentation: root `AGENTS.md`, this index, this topic and the reusable guide
  were written by authorized Bridge file writes (Codex parent, existing Claude
  worker, fresh explicit task grants). Live-write and parent verification evidence
  is recorded in the implementation report.
- Git: baseline `ec5ab9727a937dc04d1a357dec1c3a22debd1ae8`; no new commit.
- No autonomous dedup, sync or new runtime task type. Significant-decision updates
  reuse this topic by convention (hash-guarded update tested). No other repository
  is adopted automatically.

## Open questions

None for the convention. Limitation: other repositories need an applicable host
allowance and their own explicit task grants before any context write.

## References

- [AGENTS.md](../AGENTS.md), [INDEX.md](INDEX.md)
- [Reusable guide](../docs/operations/project-markdown-context.md)
- [Permission fixtures](../tests/adapters/test_markdown_context_permissions.py)
- [Host allowance](../config/runtime-capabilities.proposal.toml)
- [Permission enforcement](../bridge/repository.py), [grant binding](../bridge/repository_worker.py)
- [Implementation report](../docs/verification/2026-11-01-persistent-markdown-context-implementation-report.md)
