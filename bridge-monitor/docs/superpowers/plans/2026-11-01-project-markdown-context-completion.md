# Project Markdown Context Completion Plan

**Goal:** Let a fresh, explicitly granted read-write task write the project's
`AGENTS.md` and `.ai-context/` Markdown notes, without widening any existing
task's effective authority.

**Architecture:** The host `[repository.write_prefixes].bridge-monitor` list is
only an upper bound. `RepositoryPolicy.for_task` narrows it to the task's exact
`repository_write_prefixes`; the bound grant is frozen (`binding` digest) for the
task's lifetime. Add exactly `"AGENTS.md"` and `".ai-context/"` to that one host
list. No code, routing, model, execution, audit or cancellation change.

**Tech stack:** Python, pytest, existing `bridge-selftest` `backend-unit` recipe.

**Spec / starting point:** `docs/operations/project-markdown-context.md` and the
existing persistent-context implementation report (2026-11-01). Supplied safety
review: stored TaskPacket comparison found six identical bindings, two denied
before and after, zero authority change.

## Global constraints

- User conditionally authorized this implementation inside the existing Bridge
  workflow; no nested workers, no plan hand-off pause, no Git staging/commit.
- Writes limited to: this plan, the focused test, the one config list, the guide.
- Do not edit root `AGENTS.md`, `.ai-context/` or the report in this task (not in
  grant). No other real repository (MRI3 etc.) is touched; fixtures use temp roots.
- `TEXT_SUFFIXES` stays unchanged; callers request exact Markdown files.

## Task 1 - permission fixture + host config (one independently testable task)

1. Write `tests/adapters/test_markdown_context_permissions.py` using real
   `TaskPacket`, `RepositoryPolicy.for_task`, `RepositoryTools`, `binding`, and the
   actual configured bridge-monitor host prefixes (roots replaced by temp fixtures).
2. Run focused tests before the config edit; expect a real `PermissionError`
   ("task write scope exceeds host grant") for explicit context grants only.
3. Append the two prefixes to the config with the current SHA. No hot reload.
4. Re-run focused tests green, then the grants/execution/selftest/router regression.
5. Small guide update: explicit new-task file grants, fresh vs frozen policy.

## Review focus

- Old/serialized grants rebind to identical digests; docs-only and context-only
  grants still cannot write `AGENTS.md`/other context files.
- Read-only: both mutation tools denied, read tool list only, no `mkdir`.
- Wrong/unselected root or traversal: rejected, no fallback to another root.
- Stale, missing or unexpected hashes rejected; no duplicates or temp leftovers.
- Secret, denied-prefix, non-text, hard-link and revoked-lease guards still apply.
- Neighbours (`.ai-context-neighbor/`, `AGENTS.md.bak`) never admitted.

Advisory: an Opus-family worker suits this authority-proof group; policy selects
the actual model.

## Follow-up completed

A fresh coordinator with exact file grants updated the existing root instructions,
index and topic. The final [implementation report](../../verification/2026-11-01-persistent-markdown-context-implementation-report.md) records the completed workflow, test
results, live-write receipts and remaining limitations.
