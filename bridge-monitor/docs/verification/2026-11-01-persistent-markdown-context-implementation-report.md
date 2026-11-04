# Persistent Markdown context implementation report

Updated 2026-11-01. Project root: `C:\AI\bridge-monitor`; Git root: `C:\AI`.
Task baseline: `ec5ab9727a937dc04d1a357dec1c3a22debd1ae8`.
The changes are uncommitted; no commit or staging operation was performed.

## Outcome

The documentation and context-write handling are implemented for Bridge Monitor.
Each explicitly selected and authorized project owns its own
`.ai-context/INDEX.md` and focused topic files. Bridge Monitor provides a reusable
convention and stores only Bridge Monitor decisions. It is never another
project's default, fallback, mirror or central context store.

Authorized sessions read the selected project's index and relevant topics, reuse
existing topics after significant decisions, and record approval separately from
implementation. Already-valid grants may make repeated updates; a new task is
not required for every write. Read-only tasks write no context in any repository.
ChatGPT sharing is manual, by uploading or pasting the relevant files.

No production Python permission, routing, model-selection, task-execution,
auditing or cancellation code changed. No other repository, including MRI3, was
modified. No context was automatically installed in another project.

## Files changed

Relative to the project root and this completion task's starting workspace:

| File | Change |
| --- | --- |
| [AGENTS.md](../../AGENTS.md) | Explicit selected-project ownership, local maintenance and permission/read-only rules. |
| [.ai-context/INDEX.md](../../.ai-context/INDEX.md) | Bridge-only index; existing single topic row retained and status updated. |
| [.ai-context/persistent-markdown-context.md](../../.ai-context/persistent-markdown-context.md) | Existing topic updated in place with decisions, reasons, implementation, checks and source/test/Git references; duplicate template replaced with guide reference. |
| [docs/operations/project-markdown-context.md](../operations/project-markdown-context.md) | Existing untracked guide updated with reusable templates, ownership and precise write-grant instructions. |
| [config/runtime-capabilities.proposal.toml](../../config/runtime-capabilities.proposal.toml) | Exactly two entries appended to only `repository.write_prefixes.bridge-monitor`: `AGENTS.md` and `.ai-context/`. |
| [tests/adapters/test_markdown_context_permissions.py](../../tests/adapters/test_markdown_context_permissions.py) | New focused fixture module: 19 cases using real task/grant/filesystem APIs. |
| [docs/superpowers/plans/2026-11-01-project-markdown-context-completion.md](../superpowers/plans/2026-11-01-project-markdown-context-completion.md) | New bounded implementation plan and completion note. |
| This report | Existing untracked report updated in place; obsolete partial-status narrative and old snapshots replaced. |

The guide and report were already untracked at task start. They were updated,
not duplicated or discarded. The only newly created non-ignored source paths in
this completion task are the permission fixture module and implementation plan.

The old proposal at
`var/receipts/project-context-ownership-20261101/proposed.patch` was reviewed.
Its report hunk failed `git apply --check` against the updated report, as expected.
It was not applied blindly: the root documents were regenerated against current
hashes, and the report was updated separately. That proposal is historical;
there is no outstanding patch application required for this implementation.

## Smallest safe permission change

The host list is an upper bound, not a task grant. The sole configuration change
adds exact `AGENTS.md` and directory `.ai-context/` to the Bridge Monitor entry.
All other bytes are preserved: removing just those two additions restores the
original configuration SHA-256
`26056829303b6daee8504e186a55433a8067959257dfdadc3ea2d7562b0d1226`.

A context write still requires:

1. An explicitly selected, approved project root and repository ID.
2. A valid read-write task grant with explicit bounded file prefixes.
3. Paths inside that grant, passing all existing containment, secret, link,
   suffix, live-lease and expected-content-hash checks.

Context tasks should request exact Markdown filenames. `AGENTS.md` is a separate
exact-file grant when its instructions need changing. Read-only grants remove
mutation tools and are denied by the mutation implementation before directory
creation. No automatic permission escalation, fallback or global adoption was
added.

Existing workers use their serialized policy snapshot and binding. A host change
does not upgrade those grants. An already-valid task can update its permitted
context files repeatedly; only a missing scope requires a new authorized grant.
Fresh coordinators load the changed host allowance through the same existing
launcher, not a new execution route.

Before changing the host allowance, the parent performed a read-only metadata
comparison of eight stored Bridge repository TaskPackets. Actual
`RepositoryPolicy.for_task` and `repository_worker.binding` produced six
identical grants and two denials both before and after the in-memory candidate.
There were zero changed-authority packets and zero unclassified packets. This
satisfied the user's condition against expanding existing-task authority.
Evidence: `var/receipts/context-completion-20261101/authority-comparison.json`.
No database or routing-policy change was made by that inventory.

## Workflow verification

| Requirement | Actual evidence |
| --- | --- |
| Appropriate Bridge-managed writes | Codex parent dispatched the existing approved Claude worker through the same launcher with fresh exact file grants. Live call `953e2551-d530-5910-93f5-ca836545b1f9` successfully wrote the three existing local files and updated the guide. |
| Read-only tasks cannot write | Fixture cases for both synthetic roots called real `write_file` and `replace_text`: denied, no context directory created, existing instruction bytes preserved. |
| Another selected project never writes to Bridge | Temporary sibling roots `project` and `bridge-monitor`; selected-project writes succeeded, unselected IDs/traversal/wrong roots were rejected, Bridge sentinel unchanged. No real other repository was used. |
| Existing permissions and routing preserved | Old/new stored-packet grant comparison, frozen/serialized task tests, unchanged production code and routing-policy digest, and exact two-entry configuration delta. |
| Existing topics updated instead of duplicates | Fixtures create then hash-guardedly update the same topic and assert one topic/index with no temp leftovers. The live worker updated the existing topic and retained the existing single index row. Semantic topic selection remains a convention, not an automatic deduplication service. |

The verified workflow is **Codex parent → existing Bridge-managed Claude worker**.
No separate Codex worker route exists or was introduced. This distinction avoids
claiming execution by an unsupported provider route.

### Tests and checks actually run

All executable tests ran through the existing structured `run_checks` recipe:
repository `bridge-monitor`, kind `bridge-selftest`, goal `backend-unit`,
offline true.

| Run | Selection | Result |
| --- | --- | --- |
| `494b3498-f4f4-4a35-88aa-dc628128acde` | New permission fixture module, before config change | Expected red: 1 failed, 18 passed; missing host allowance raised `task write scope exceeds host grant`; no collection/syntax error. |
| `6eb8245a-b8a8-4d0f-9554-b40c6930dc4a` | Same fixture module, after config change | 19 passed, zero failures/errors/skips, exit 0. |
| `7fb0c508-97b8-4aa2-989c-ff4e59d6c6f1` | New fixture plus existing grants, repository execution, Bridge selftests and router tests | 147 passed, 46 skipped, zero failures/errors, exit 0; partial verification because of skips. |

Regression selection:
`tests/adapters/test_markdown_context_permissions.py,tests/adapters/test_repository_grants.py,tests/adapters/test_repository_execution.py,tests/adapters/test_bridge_selftests.py,tests/router`.

The 46 skips comprise 45 database-dependent tests without
`BRIDGE_MONITOR_TEST_DSN` in the backend-unit recipe and one symlink-creation
privilege skip. No warnings were shown. The parent independently read the run
metadata and logs under
`var/logs/repository/6bd3be5e-aa46-55b4-a0a8-a92ac317af8f/`.
No full-suite, complete database, or symlink integration pass is claimed.

The parent also inspected the actual changes, confirmed the precise configuration
delta, and checked all 18 links in the four instruction/context/guide documents,
their whitespace and code fences. The final report is read back and checked
against its embedded source snapshots after writing; the final preservation
receipt is `var/receipts/context-completion-20261101/final-verification.json`.

An independent supplied-context read-only review found no Critical or Important
blocker. It ran no tests itself. The parent checked that the reviewed config and
fixture hashes remained unchanged; concurrent authorized documentation edits
were disjoint. Review call: `65462317-7410-58bc-ae44-8be8a3178774`.

### Live write receipts and recovery

Live operations are recorded at
`var/logs/repository/953e2551-d530-5910-93f5-ca836545b1f9/operations.jsonl`;
all four successful operations have the same bound policy digest
`b524124a8fd5b9cf9824508fe4598535f202a9384941cdf4fd168c36dca4b229`.

A prior fresh-grant attempt (`0b0b660c-6da0-57cf-b859-79345462b82e`) was admitted
but all writes were rejected because the parent supplied uppercase hashes to the
existing lowercase-hash comparison. It changed nothing. After confirmed
completion with no held slot, a new task identity used refreshed lowercase hashes
and succeeded. No hash guard or runtime behaviour was weakened. Editorial call
`c00201d3-4f38-5232-ad03-0289399f0b44` then clarified valid-grant reuse and added
source/test references.

The original desktop MCP process cannot acknowledge a separate one-shot
launcher's owned result. No cross-process acknowledgement receipt is claimed;
the parent instead independently checked the durable receipts and actual files.

## Remaining limitations and approvals

- Existing long-lived MCP/coordinator processes keep their old configuration.
  Use the existing normal reload/new-session mechanism, or the same fresh
  launcher already verified here, for the new host allowance. No process was
  forcibly restarted and no active task was upgraded.
- The host `.ai-context/` entry retains the normal approved-text suffix rules,
  which include formats besides Markdown. Exact per-task Markdown file grants
  keep context maintenance narrow; no new host-level Markdown-only ACL was added.
- Notes are maintained by authorized sessions following instructions. There is
  no automatic background writer, ChatGPT sync, semantic duplicate detector or
  universal client enforcement.
- The existing filesystem tools are not an OS sandbox; existing link-race and
  trusted-test execution limitations were not redesigned. The symlink and
  database skip coverage remains explicitly unverified.
- Other repositories require their own applicable host allowance, explicit
  selection and task grants. This task made no automatic changes to them.

No additional permission proposal requires approval within the requested scope.
The two-prefix host change was applied under the user's conditional authorization
after the existing-authority comparison and was covered by red/green fixtures.

## Routing and audit provenance

All substantive execution was Bridge-delegated. Direct parent operations were
limited to orchestration, read-only Bridge configuration/task-metadata management,
and independent verification. Requested Bridge entry profile remained `sonnet`;
the existing policy selected the actual workers. The routing-policy digest
remained `6a1f9a618d106f057b3d3afca379e7783765cc6682c21ca27d4c778dc4e64c4b`.

| Phase | Call | Reported model |
| --- | --- | --- |
| Supplied permission-safety review | `24bbe311-f1f0-51b4-ae07-7e87da87bbea` | `claude-opus-5-5` |
| Config and fixture implementation | `6bd3be5e-aa46-55b4-a0a8-a92ac317af8f` | `claude-opus-5-5` |
| Fresh-grant ownership docs | `953e2551-d530-5910-93f5-ca836545b1f9` | `claude-sonnet-5-5` |
| Editorial precision | `c00201d3-4f38-5232-ad03-0289399f0b44` | `claude-sonnet-5-5` |
| Independent permission review | `65462317-7410-58bc-ae44-8be8a3178774` | `claude-sonnet-5-5` |

An initial bounded read-only scan reached its deadline without a result; its
termination and unchanged files/index were checked before the narrower supplied
review. No uncertain worker was blindly replayed. No token totals, costs or
savings are inferred or included here.

## Current source snapshots for ChatGPT analysis

Upload this report as one file. The four snapshots below match the live
instruction/context/guide text after newline normalization. Live files remain
canonical; source/test/log references are local paths, not remotely accessible
links in ChatGPT. Suggestions from another conversation remain proposals until
approved and persisted through an authorized task.

### AGENTS.md

````markdown
# Project context

This file governs Bridge Monitor work only. Each explicitly selected project owns
its own `.ai-context/INDEX.md` and topics in its selected root. Never use Bridge
Monitor as a fallback or central store for another project's notes, and never
infer a project root from the Bridge installation, working directory or a Git
parent root. Reusable guidance: [project Markdown context](docs/operations/project-markdown-context.md).
Nothing here adopts the convention in another repository or edits one.

Before substantive work, read [.ai-context/INDEX.md](.ai-context/INDEX.md) and the
topics relevant to the task. Search existing topics and project documentation
before creating a new note.

When a significant architectural or technical decision is made, revised or
superseded, or its implementation/check status materially changes, create or
update the relevant topic and index before handoff. Follow the
[context convention](.ai-context/persistent-markdown-context.md). Skip trivial
details and routine progress.

Updates must fit the existing task authorization and execution path. In read-only
tasks, report suggested updates without writing them. If a write is blocked,
report the pending update; do not widen permissions or bypass Bridge.

Parent instructions and the existing Bridge policies remain in force. Context
notes record knowledge; they grant no authority and change no Codex/Claude
routing, model selection, task execution, permissions, auditing or cancellation
behaviour.
````

### .ai-context/INDEX.md

````markdown
# Bridge Monitor context

Project root: `C:\AI\bridge-monitor`. Git root: `C:\AI`.
Read the relevant topic before work; update its existing row when it changes.

This index records Bridge Monitor decisions only. Each other explicitly selected
project owns its own local notes in its own `.ai-context/`; do not mirror or
redirect them here. The canonical
[reusable guide](../docs/operations/project-markdown-context.md) covers project
selection, adoption and write grants.

| Topic | Decision | Implementation | Reviewed |
| --- | --- | --- | --- |
| [Persistent Markdown context](persistent-markdown-context.md) | Approved; project ownership clarified | Implemented; 19 permission fixture tests verified (approval is separate) | 2026-11-01 |

For an existing or new ChatGPT conversation, attach or paste this index and the
relevant topic, plus necessary excerpts from linked sources. Include the project
identity, Git revision and any uncommitted changes. ChatGPT has no automatic access
to these local files. Suggestions remain proposals until explicitly approved;
bring approved decisions back to an authorized Codex session to persist them.
````

### .ai-context/persistent-markdown-context.md

````markdown
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
````

### docs/operations/project-markdown-context.md

````markdown
# Project Markdown context convention

Reusable guidance for keeping significant technical decisions in Markdown notes
that Codex and ChatGPT can share. It is ordinary documentation. It adds no gate,
dispatch, routing, model, permission, audit or cancellation behaviour, and it
cannot be enforced by Bridge.

## Ownership

- Each project owns its context at `<project-root>/.ai-context/INDEX.md` plus
  one `topic.md` file per focused topic.
- `<project-root>` is the repository the task explicitly selects and is authorized
  to work on. Never infer it from the Bridge installation, the server working
  directory or a different Git top-level. Example: a parent Git root `C:\AI` and a
  selected project `C:\AI\bridge-monitor` are different; notes for the selected
  project go under the selected project root.
- Decisions about a selected project live only in that project. Bridge Monitor
  notes cover only Bridge Monitor work. They are never a fallback, central store,
  mirror or aggregate of another repository's context.
- A task spanning several explicitly selected roots keeps separate notes in each
  owning root, under that root's own permissions. Selection is not authorization.
- If no project is selected, identify or request the target through the existing
  task flow before attempting any context write.

## When to read and write

1. Read the governing repository instructions, the project's `INDEX.md` and the
   relevant topics before substantive work.
2. Create a missing index or topic only for significant authorized work, and only
   when existing write grants cover that exact root and path. Do not bootstrap
   `.ai-context/` merely because a repository was selected. Do not change allowed
   prefixes or routing, and do not copy instructions or configuration into other
   repositories automatically.
3. Read-only tasks never create or edit context directories, indexes, topics or
   instructions in any repository. Report the suggested update in the response;
   do not write it in Bridge instead.
4. If a write is not permitted, report the update as pending for the owning
   project. Use the existing authorization flow; this convention adds no approval
   requirement beyond current policy.

## Write grants

- A host `write_prefixes` entry (for Bridge Monitor: `AGENTS.md` and
  `.ai-context/`) is only an upper bound. It grants no task anything by itself.
- A context write needs a valid task grant with explicit `repository_access` read-write,
  the selected authorized `project_root`, and exact Markdown file grants, e.g.
  `{"<repo-id>": [".ai-context/INDEX.md", ".ai-context/<topic>.md"]}`. Add the
  exact `AGENTS.md` filename only when that file must change. Avoid requesting the
  whole `.ai-context/` directory; the host entry keeps the normal approved-text
  file rules, so exact grants are what keep the scope narrow.
- No new task is needed for every write when the current grant already includes
  the file. Runtime is unchanged and allows repeated hash-guarded updates, as the
  fixtures prove (`tests/adapters/test_markdown_context_permissions.py`;
  enforcement in `bridge/repository.py`, grant binding in
  `bridge/repository_worker.py`). A new grant is required only when the existing
  scope does not cover the file.
- A task's grant is bound when it starts and stays fixed. Changed host config
  cannot silently upgrade old grants. After a runtime change, use a fresh
  coordinator (same launcher, new MCP session); this is not a new execution path.
- Other repositories adopt nothing automatically; each needs an applicable host
  allowance and its own explicit task grants. Authorized sessions maintain notes;
  no automatic note-writing or synchronization service is added.

## What to record

Significant decisions only: approved choices versus proposals, implementation
status, checks run and not run, reasons, trade-offs, open questions, and source,
test and Git references. Update the existing topic instead of adding duplicates
or trivia. Canonical specifications and runbooks stay authoritative; link to
them. Never store secrets or raw chat transcripts. Approval comes from the user;
writing a note or implementing an idea is not approval.

## Reuse

Reuse the stanza and skeletons below only where that project's changes are
authorized. Sharing with ChatGPT is manual: attach or paste the index, the
relevant topic and needed excerpts, with project identity, Git revision and any
uncommitted state. There is no automatic synchronization, service, retrieval or
runtime behaviour.

### Minimal project `AGENTS.md` stanza

```markdown
# Project context

Before substantive work, read `.ai-context/INDEX.md` and relevant topics in this
project. Update them only for significant decisions or status changes, and only
within this task's existing write authorization. Keep notes in this project's
own `.ai-context/`. In read-only tasks, report suggested updates without writing.
If a write is blocked, report the pending update. Notes grant no authority and
change no permissions, routing or task execution.
```

### Index skeleton

```markdown
# <Project> context

Project root: `<root>`. Only this project's decisions are recorded here.

| Topic | Decision | Implementation | Reviewed |
| --- | --- | --- | --- |
| [<Topic>](topic.md) | Approved / Proposed / Superseded | <state> | <YYYY-MM-DD> |
```

### Topic skeleton

```markdown
# <Topic>
Project: <identity/root>. Purpose/problem: <context>. Last reviewed: <date>.

## Decisions
Approved: <choice; approval source/date, or none>.
Proposed (discussion required): <options, or none>.

## Reasoning and trade-offs
<Why; alternatives and costs.>

## Implementation and verification
State: <not started / partial / implemented / verified>.
Checks run: <commands/results>. Not run: <checks and reasons>.
Git: <actual revisions; uncommitted work>.

## Open questions and references
<Questions, next step, and project-relative source/test/doc links.>
```
````
