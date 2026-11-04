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
