# Persistent Markdown context

Project: Bridge Monitor, `C:\AI\bridge-monitor` (Git root `C:\AI`).
Purpose: share significant technical decisions between Codex and ChatGPT
conversations without depending on chat history. Last reviewed: 2026-11-01.
Source paths below are relative to the project root unless stated otherwise.

## Decision and approval

**Approved:** the user's request on 2026-11-01 establishes this lightweight
Markdown convention: one index and small files for individual topics, maintained
as decisions change. The user then replied "yes" to direct application of the
reviewed three-file patch and documentation checks. This approval does not cover
unrelated proposals or alter runtime policy. **Proposals:** none.

Use **Approved**, **Proposed (discussion required)** or **Superseded** for
decisions, with the approval source and date when known. Unknown approval means
Proposed. Record proposals separately from approved choices. User approval
determines decision status; writing a note or implementing an idea is not approval.
When a choice is superseded, retain a brief reason and link to its replacement.

## Maintaining topics

- Read the index and relevant topics before substantive work. Search existing
  topics and canonical documentation first; update them rather than duplicate them.
- Record decisions that would change a later session's design or technical work:
  important constraints, chosen/rejected options, trade-offs, material evidence,
  unresolved questions, or changed implementation status. Skip trivial edits,
  routine progress, raw transcripts and speculative historical backfill.
- Use stable descriptive filenames, such as `topic-name.md`, and one focused topic
  per file. Keep the index to navigation and a short state summary.
- Make each topic understandable by itself: name the project, explain the problem
  and current choice, state its approval and implementation separately, and link
  relevant source files, tests, documents and actual Git revisions.
- Update relevant topics and the index before handoff after significant changes,
  only through the existing authorized execution path. Read-only work reports
  proposed updates. Blocked writes are reported, never treated as permission
  to change grants, routing or auditing.
- Keep detailed specifications/runbooks canonical. Link rather than copy them.
  Check facts against current source; note stale or conflicting evidence.
  Do not store secrets or private conversation dumps.

## Reasoning and trade-offs

Small repository files are easy to review, revise and share selectively. They
require deliberate maintenance and manual sharing with ChatGPT. Review dates,
source links and Git references expose stale context; they do not prove freshness.
One topic per file avoids a growing session log and duplicate decisions.

These notes are separate from [BM-06 pinned context](../docs/specifications/pinned-context.md).
They add no loader, synchronization, automatic prompt assembly or runtime input.
Existing [runbook](../docs/operations/runbook.md),
[repository execution](../docs/operations/repository-execution.md), and
[project access guidance](../docs/operations/project-access-and-queue-maintenance.md)
remain canonical for Bridge behaviour.

## Implementation and verification

- State: the three convention files are applied; documentation checks passed.
- Baseline revision: `5f5d8556a7c495ca90803983bc1b4a195dca9b2b` in Git root
  `C:\AI`. Existing staged/unstaged work is unrelated. This change is uncommitted;
  there is no implementation commit to cite.
- Checks on 2026-11-01: full new-file diffs inspected; nine local Markdown links
  resolve; fences are balanced; no trailing whitespace. Ran
  `git diff --no-index --check -- /dev/null <file>` for each new file (exit 1
  indicates new-file differences; no whitespace diagnostics).
- SHA-256 comparison: all 526 pre-existing project files unchanged. Existing
  Git status entries and the repository-wide staged index are preserved.
- Not run: executable test suite/build, because only Markdown documentation changed.
- Source: [session instructions](../AGENTS.md) and [index](INDEX.md).
  No executable source, tests, configuration or runtime behaviour is changed.

## Open questions

No unresolved design questions for this convention. Existing access limitation:

The current Bridge host write prefixes exclude root `AGENTS.md` and
`.ai-context/`. The user approved direct Codex execution for this application.
Future sessions must use an already authorized path or report pending note
updates. This convention does not expand permissions.

## Sharing and reuse

Attach/paste this topic and necessary linked excerpts in any ChatGPT conversation;
include project identity, revision and uncommitted state. Local relative links
need their target excerpts when the reader cannot access the checkout. Treat new
suggestions as proposals and persist them only in an authorized session; never
assume chat history or automatic synchronization.

To adopt this convention elsewhere, separately authorize that repository's
changes, reuse the generic session guidance/index/template, and adapt identities,
paths and existing documentation. No project-specific runtime logic is required.

## Template for a new topic

Use this within a new, focused file only when no existing topic fits:

```markdown
# <Topic>
Project: <identity/root; Git root if different>.
Purpose/problem: <enough context to read independently>.
Last reviewed: <YYYY-MM-DD>.

## Decisions
Approved: <choice; approval source/date, or none>.
Proposed (discussion required): <options/questions, or none>.
Supersedes / superseded by: <linked choice and reason, or none>.

## Reasoning and trade-offs
<Why this choice; alternatives considered and their costs.>

## Implementation and verification
State: <not started / partial / implemented / verified; independent of approval>.
Checks run: <commands, results and evidence>. Not run: <checks and reasons>.
Git: <actual baseline/implementation revisions; identify uncommitted work>.

## Open questions
<Unresolved questions and next bounded step, or none.>

## References
<Source/test/doc paths relative to the named project, with meaningful labels.>
```
