# Codex → Claude implementation bridge

This local stdio MCP server launches the installed Claude Code CLI using its
existing authentication and model preference. It reuses the Python MCP runtime
at `C:/AI/gpt-claude-mcp-bridge/.venv/Scripts/python.exe` (Python 3.13; the
helpers are standard library only and the tests need no extra dependency).

The Codex client starts the server automatically. After the project MCP
configuration is added, restart Codex to load the tools. No background service
needs to be started manually.

> **Activation state.** Editing these files does not change the registered
> process: an already-running bridge keeps serving the older, memory-only code
> until it is restarted normally at some later time. No restart or
> re-registration is part of this change.

## Workflow

1. The human authorizes the task and design.
2. Codex optionally calls `capture_source_state(source_paths)` to get the exact
   source digest it is binding to.
3. Codex calls `implement_changes(task, scope, acceptance_criteria[, resume_job_id][, contract])`.
4. The tool returns a job ID immediately. Codex polls `implementation_status`.
5. Codex inspects the actual changes and verifies relevant checks independently.
6. Codex records its judgement with `record_review(...)`, or corrects work with
   `implement_changes` and an explicit `resume_job_id`.

Only one job runs at a time, across processes: a cross-process `active-job` lock
is taken first and all preflight (root, repository, contract, approval, revision,
resume, source evidence) runs while holding it, before the initial receipt is
written and before anything is dispatched. A failed preflight always releases the
lock and leaves no running entry. Resume is explicit and refuses another checkout
or branch. Fresh requests use fresh Claude sessions. Avoid other agents editing
the same checkout while a job runs.

## Tools

| Tool | Purpose |
|---|---|
| `implement_changes(task, scope, acceptance_criteria, resume_job_id='', contract=None)` | Existing signature; `contract` is the only addition and is optional. Returns `job_id`, `status: running`, `binding` (`bound`/`unbound`) and `receipt`. |
| `review_changes(task, scope, acceptance_criteria, additional_read_roots=[], source_paths=[])` | Fresh read-only review. CLI-restricted Read/Glob/Grep only; extra absolute Git roots are read access only. Poll `implementation_status` for findings and before/after checks. |
| `implementation_status(job_id)` | Live job, or the durable receipt after a restart. Includes `evidence` (review staleness) and `human_acceptance`. |
| `capture_source_state(source_paths=[])` | Read-only. Returns the source `digest`, `branch`, `head`, counts and what the snapshot covers. |
| `record_review(job_id, verdict, concerns, verification, reviewer, reviewed_source_digest)` | Appends caller-supplied review metadata to a finished receipt. Runs nothing. |
| `list_receipts(limit=20)` | Newest-first content-free summaries (at most 50), including interrupted and unavailable receipts. |

Calls without `contract` behave as before; their receipts are labelled `unbound`
and grant no approval. A legacy resume stays compatible but never gains a
contract binding.

### Contract binding

`contract` must be an object with **exactly** these keys:

| Key | Meaning |
|---|---|
| `task_id` | Task identifier (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`); the contract title must begin with it at a word boundary. |
| `contract_path` | Repository-relative `.md` file. No traversal, drive/UNC, stream or Windows device names, and no link components. |
| `contract_sha256` | Lowercase SHA-256 of the contract bytes (max 256 KiB, UTF-8). |
| `approval` | The exact text of the contract's `Approval:` line value (single line, max 1000 characters). |
| `base_revision` | Full lowercase commit hash; must equal the contract's `Base revision:` and the checkout `HEAD`. |
| `source_paths` | Explicit files or directories whose state matters, **including ignored ones** (the target repository may contain local-only sources). At most 100. |
| `source_digest` | The digest from `capture_source_state` for the same `source_paths`; must equal the current source state. |

Supported CTX Markdown header subset (nothing is inferred from prose):

- the first `# ` line is the title;
- `- Status: …`, `- Approval: …` and `- Base revision: …` bullet lines that appear
  **before the first `## ` heading** and **outside fenced code blocks**;
- a fenced block opens with at least three backticks or tildes and closes only
  with the same character, at least as long, followed by nothing but whitespace;
- a duplicated header line is ambiguous and refused;
- `Status` must be exactly `Approved`; `Approval` must be present, not `pending`
  and not a `<placeholder>`, and must equal the supplied `approval`.

Any mismatch, a Proposed/pending contract, a moved `HEAD` or a changed source
refuses the call before launch. Text matching is evidence that the caller's
binding agrees with the contract; it is **not** authentication of a human
decision. The caller remains responsible for actual human authorization.

## Durable receipts

Receipts are content-free JSON files, one per job, in `.receipts/` below this
directory (local runtime data, outside the application checkout). An absolute,
existing directory may be set with `CLAUDE_IMPLEMENTER_RECEIPT_ROOT`; the store
is then `<that directory>/.receipts`. The store is created on first write, never
on import.

Stored: schema version, job/task/session/parent identities, timestamps, state and
sanitized outcome code, denial count and tool categories, contract path/digest/
approval reference, branch/HEAD, source digests and counts, reported numeric
usage counters and model names, and review metadata (verdict, reviewer label,
concern IDs with dispositions, short verification command metadata, outcome and
exit code). **Never stored:** task/scope/criteria text, Claude's result text, raw
stdout/stderr, denied tool input, credentials or source content. Each file has a
SHA-256 integrity value that detects accidental corruption and partial edits; it
is not authentication. Limits: 512 KiB per receipt, 20 reviews, 50 concerns,
20 verification entries.

Writes are atomic (temporary file, fsync, replace) and validated; the previous
record stays authoritative if a write fails. Reviews are append-only history.

### States and failure semantics

- `running`, `completed`, `needs_attention` (denied commands), `failed`,
  `timed_out`, `interrupted`. `completed` means the CLI reported success; it is
  not review, verification or acceptance.
- **Initial receipt cannot be written:** nothing is launched; the call fails.
- **Final receipt cannot be written:** status `persistence_error`; the on-disk
  receipt stays `running` and later reads as `interrupted`. The CLI-reported
  outcome is labelled separately (`receipt.reported_outcome`), never as durable.
- **Receipt missing, corrupt, tampered, oversized, unknown schema or reached
  through a link:** reported as `receipt_unavailable` with a sanitized `reason`.
  A job still cached in memory is *not* reported as durably complete in that
  case: `status` becomes `receipt_unavailable`, the in-memory outcome is kept as
  `reported_status`, and `durable_assurance` is `uncertain`.
- **Bridge restart:** receipts left `running` become `interrupted`, with an
  explanation and the source state at recovery. They are never relaunched or
  inferred successful. If another bridge instance holds the `active-job` lock the
  receipt is left untouched and reported as `running`.
- **Deliberate resume** from a durable receipt requires a `completed` or
  `needs_attention` job with a session, the same root and branch, and (bound
  jobs) the unchanged contract, approval, base revision and source digest. It is
  refused before launch otherwise.

## Source evidence

A snapshot hashes HEAD, branch, tracked and non-ignored working-tree files
(including untracked and deleted ones, from Git NUL-separated output) plus each
declared `source_paths` entry. It stores identities (SHA-256, sizes), never
content, and fails closed on unreadable or unsafe entries. The receipt store and
its temporary writes are excluded so they cannot invalidate their own snapshot.
Other ignored files are not covered unless declared.

It is a point-in-time observation, not continuous immutability and not OS
isolation: a change between the snapshot and a later action is only detected by a
later snapshot.

## Reviews

`record_review` requires `reviewed_source_digest`, the 64-hex digest from
`capture_source_state` that the reviewer actually examined. The source digest is
recomputed at recording time and the review is refused if it differs; a blank or
malformed value is refused. `verdict` is `no_blocking_concerns`,
`changes_requested` or `inconclusive`. Concerns are `{id, disposition}` with
`open|fixed|accepted_risk|not_applicable|deferred`. Verification entries are
`{command, outcome[, exit_code]}` with `passed|failed|not_run|denied`.

A later change to the source or contract marks earlier reviews `stale` on status
(`source_changed`, `contract_changed`, or unknown when evidence is unavailable);
the historical review is never edited. **A review is recorded evidence, not
human acceptance, approval, merge or publication.** `human_acceptance` stays
`{"state": "not_recorded"}`; no tool sets it. Implementation completion, review
verdict and human acceptance are three separate things.

### Disclosure limits

Verification `command` is metadata only: a short single line. The server rejects,
heuristically, credential-looking strings, `NAME=value` and whitespace-separated
credential names with values (`export X_TOKEN value`, `--password value`),
bearer/authorization values, `-u user:pass`, URLs with embedded credentials,
JSON-like objects/arrays and `stdout=`/`stderr=` style payloads. These are
fail-closed heuristics that can both miss secrets and refuse harmless text. The
caller remains responsible for what it sends; never put output, source or
secrets in review metadata.

## Path safety

Repository root, receipt store base and every absolute ancestor component must be
real directories: a symbolic link or junction anywhere in the chain is refused
before any resolution or read, as are linked stores, lock files, contracts and
declared source paths. If the bridge directory itself is reached through a
junction, configure `CLAUDE_IMPLEMENTER_RECEIPT_ROOT` to a real directory. The
checks run before each access; they are not protection against a link swapped in
between a check and the following system call.

## Permissions and boundaries

### Read-only review

Use `review_changes` when the human requests review or says "no changes". Analysis
may use Claude or the authorized Skynet worker; applying suggestions requires a
separate implementation instruction. Do not silently substitute `implement_changes`
if review mode is unavailable. An explicit exception is scoped to that review.

Review launches use `--restricted --safe-mode`, deny-by-default (`dontAsk`) permissions, no permission
prompts, an explicit `Read,Glob,Grep` tool surface, disabled custom hooks/skills and
strictly empty MCP configuration. Bash, PowerShell, Edit, Write, NotebookEdit,
agents and permission-mode transitions are unavailable. This is enforced by the
Claude CLI's tool capabilities, not just a prompt; it is not an OS filesystem jail.
Safe mode retains the CLI's authentication and model selection. Repository guidance
is read explicitly rather than loaded as executable customization.

Additional read roots must be absolute, real, exact Git checkout roots. Review
refuses to launch without captured baseline evidence. It compares tracked and
nonignored source in every root afterward; ignored primary files can be included
with `source_paths`. Changed or unavailable evidence results in `needs_attention`,
even if Claude reports success. `review_verification` contains the live per-root
results; durable receipts retain the execution mode and failure outcome. Cross-root
detail and review text are memory-only, as with ordinary implementation results.
Review sessions cannot be resumed for implementation, including after bridge restart.
CLI runtime logs and bridge receipts are outside the target source snapshot.

The CLI must support the restricted/safe flags. An older CLI fails rather than
falling back to edit-capable execution. Reload/restart the MCP connection after
installing this update to discover `review_changes`; running servers retain old code.

### Implementation

Claude runs with `acceptEdits`, without bypassing permissions, and reads the
normal Claude settings and repository instructions. Commands that would prompt
are denied (`--permission-prompts none`) and reported as `needs_attention`.
Existing allowed commands can run; Codex must inspect their results. This is
application-level permission control, not an OS sandbox or filesystem jail.
The supplied scope is an instruction, not enforced path containment.

MCP configuration is explicitly empty and strict (`--strict-mcp-config`,
`{"mcpServers":{}}`) to prevent recursive calls through the GPT architect bridge.
Available built-in tools are Read, Edit, Write, Glob, Grep and Bash. Direct git
commit/push/add/reset/clean Bash commands are denied. The system instructions
also prohibit staging, committing, pushing, publishing, installing dependencies
and changing agent/security configuration; these instructions are not an
OS-level guarantee against indirect commands.

Claude's tool results are untrusted implementation evidence. A successful CLI
result is not a reviewed change. Review the real diff before claiming completion.
Descriptions and results may contain repository-sensitive material; use the same
disclosure constraints as the parent task. Raw stderr is never returned and no
result text is persisted. Usage counters and model names are recorded exactly as
the CLI reports them, with missing values left unknown; model selection is not
verified, and no cost, savings or remaining allowance is derived.

Jobs time out after 15 minutes; the spawned process tree is terminated and
partial changes remain for inspection (the receipt records before/after source
evidence). Keep Codex open while a job runs.

## Verification

Run from the repository root with the Python 3.13 runtime above. Fixtures use
temporary Git repositories and receipt stores, a mocked Claude process and no
signed-in account. Tests that need symbolic links or junctions skip when the
platform denies them; deterministic mocked link checks still run.

```powershell
$py = 'C:/AI/gpt-claude-mcp-bridge/.venv/Scripts/python.exe'
& $py -B -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge -p 'test*.py'

# focused modules
& $py -B -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge -p test_receipts.py
& $py -B -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge -p test_source_snapshot.py
& $py -B -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge -p test_contract_binding.py
& $py -B -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge -p test_flow.py
& $py -B -m unittest discover -s C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge -p test_mcp_transport.py
```

`test_mcp_transport.py` starts the real server module in a child process behind a
temporary shim with a fake Claude launcher and speaks MCP over stdio:
initialization, discovery of the legacy and new tools, status/preflight
refusals, a faked dispatch and review, and restart recovery. It never starts the
Claude CLI.

The live smoke test is optional and is **not** part of the suite:

```powershell
& $py -B C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge/smoke_test.py
```

It exercises MCP initialization, discovery (the required legacy tools must be
present; additional tools are allowed), asynchronous dispatch and status polling
with a read-only Claude request. It uses the signed-in Claude account and
consumes a small amount of Claude usage, so run it deliberately.

## External source ownership

Source, receipts and activation history live below this directory, outside
application repositories. The client supplies its target checkout through
`CLAUDE_IMPLEMENTER_ROOT`; `CLAUDE_IMPLEMENTER_RECEIPT_ROOT` points to
`C:/AI/gpt-claude-mcp-bridge-v2/tools/claude-bridge`.

Receipts retain their original bytes and historical path references. The relocation
does not refresh their source evidence, approve work, or authorize an automatic resume.
Repository-specific bridge plans and handoff history are under
`../../docs/repository-history/mri-java-services/codex/docs/`.
