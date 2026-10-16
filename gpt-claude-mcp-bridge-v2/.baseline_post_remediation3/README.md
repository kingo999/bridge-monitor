# GPT Architect ⇄ Claude Code MCP bridge — v2 (review candidate, third remediation)

**Status: isolated candidate. NOT registered, NOT activated, NOT committed.** v1
(`C:\AI\gpt-claude-mcp-bridge`) is untouched and remains the live bridge. Decisions D1, D3, D8
and the design points P2/P3 are pending (human); implementing a conservative candidate is not
approval to activate it. Evidence for this candidate is in `REVIEW_PACKAGE.md` and
`docs/evidence/`, is bound to a source hash, and is **implementer-executed** (mocked CLI or
model-free); results recorded for earlier candidates are *not* evidence for this code.

Same contract as v1: a local stdio MCP server that lets Claude Code consult a GPT architect
through the **ChatGPT-authenticated Codex CLI** (subscription allowance only; no API client,
API key or API fallback). Exactly two tools: `consult_architect(question: str)` and
`review_implementation(focus: str)`; text responses. GPT advice is not approval.

```
architect_bridge.py        MCP entry point (same invocation as v1)
bridge_admin.py            local administration CLI (not MCP tools)
bridge/                    implementation package
tests/  tools/  docs/      tests + fake CLI, helper/evidence scripts, notes and evidence
requirements.lock.txt      hash-pinned Python dependency set (win_amd64, py3.13)
```

## Threat boundary and the precise read-only claim

**What the source does.** On every fresh and resumed call the bridge pins
`sandbox_mode="read-only"` and `approval_policy="never"` in the argv, refuses to build an argv
that would loosen either (guard-checked), and audits the capability policy before inference.

**What has been shown for *this* source.** Unit tests (mocked CLI, model-free checks against
the installed CLI) assert the argv pinning, failure handling and the behaviours described
below. **Native shell / `apply_patch` write rejection has not been live-verified for this
source hash.** Rejections recorded earlier (`docs/history/`) apply only to earlier candidates
and the conditions they were run under.

**This is not a claim that**

* all capabilities are read-only, or that no external side effects occur: web access,
  `view_image`, skills/instruction injection, and any MCP server on the allow-list are
  outside the filesystem sandbox (see *Capability containment*);
* repository **confidentiality** follows from filesystem sandboxing: repository content is
  sent to OpenAI as part of every review, by design;
* the bridge writes nothing outside the reviewed repository: bridge state
  (`%USERPROFILE%\.gpt-architect-bridge-v2`), Codex's own history/sessions under
  `~/.codex`, and Windows sandbox setup (ACL grants for sandbox accounts on the repository
  and runtime files) are written by the bridge or by Codex as part of normal operation;
* the restrictions hold on unsupported Codex versions, or when the sandbox backend is
  unavailable (the bridge then refuses; it never degrades silently);
* every bridge-originated execution route is inside that sandbox: the bridge itself runs
  host-side `git` and the model-free `codex sandbox` preflight. Those are constrained as
  described under *Host-side Git containment* and *Sandbox preflight*, not by the Codex
  sandbox.

## What is enforced, requested, or unverified

Every capability control is reported at one of three evidence levels (`bridge_admin.py
status`, telemetry `capabilities`):

| Level | Meaning | Examples |
|---|---|---|
| **resolved** | the CLI's *own effective-config readout* showed the control applied at the audited moment (`codex features list` / `codex mcp list --json` with the overrides) | feature gates: hooks, plugins, apps, browser/computer use, **multi_agent** (delegation), **image_generation**; MCP servers |
| **requested** | passed to the CLI; the CLI offers no readout to confirm it | `web_search="disabled"` (a key in the CLI's config schema; the observed `web__run` tool is not mapped to it); the read-only sandbox / approval flags (argv-pinned and guard-checked) |
| **uncontained** | no control exists or none is used | `view_image` (local image read), goals/sleep, skills under `~/.codex` and `~/.agents`, **any allow-listed MCP server** |

* **Snapshot scope.** "resolved" means the CLI said the gate/server was off in a readout
  taken before the call; it is **not** evidence that the execution used the same
  configuration, and **not** that tool removal was observed at runtime. The model-visible
  prompt text still mentions delegation with the gate off, so prompt text is not evidence
  either way. Runtime tool availability is only observable with a live tool listing (proposed,
  not executed).
* **Residual race (documented, not eliminated).** The CLI has no supported MCP *deny-all*
  (`-c mcp_servers={}` has no effect on the readout), so each known server is disabled by
  name. A server added by another process between the final readout and `codex exec`
  loading its configuration is not contained. The bridge narrows this: the MCP set is read
  again immediately before exec (`capability_audit/mcp_changed_before_exec` refuses a drift)
  and once more after the turn (`mcp_config_changed_during_call` is bannered). It cannot close
  the final window.
* The MCP allow-list (`GPT_ARCHITECT_MCP_ALLOW`) is **empty by default**. An allowed server
  is *not contained*; it is derived from the *final* readout, listed under `uncontained`, and
  named in every returned answer (`[bridge-warning]`), in telemetry
  (`capabilities.mcp_allowed`, warnings) and in `status`, without paths or secrets.
* **Fail closed:** a missing required control (`required_control_missing`), an unparseable
  `features list` / `mcp list` schema or a duplicated feature row
  (`*_schema_incompatible`), a gate still on after the override
  (`feature_disable_not_effective`), or an MCP server still enabled (`mcp_still_enabled`)
  aborts before inference.
* **Web:** disabled by request; there is deliberately **no exception mechanism**. Any web
  exception needs a written egress/research policy approved by the human — pending (D8).
* **Delegation / image generation:** controlled by their own gates and reported separately.
  If a future CLI lacks a gate the call fails closed rather than claiming containment.

## Host-side Git containment

The bridge runs `git` on the host (outside the Codex sandbox) to fingerprint the repository
and to choose a preflight file. Two separate boundaries are handled.

**Which program runs.** The MCP server's working directory is the reviewed project, and both
Windows `CreateProcess` and `shutil.which` look in the current directory first, so a bare `git`
could resolve to a file the repository ships and run it with your full permissions before any
flag matters. `bridge/execpath.py` is the only place executables are resolved: it considers
**absolute `PATH` entries only**, never `""`, `"."` or a relative entry, and skips any entry that
is or lies inside the current directory, `CLAUDE_PROJECT_DIR` or the reviewed repository. It
is used for `git`, for the `codex` launcher and `node` (an explicit `GPT_ARCHITECT_CODEX_PATH` is
your own statement of trust and is not searched), and `taskkill`/`tasklist` are addressed by
their absolute `System32` path. If no trusted `git` exists the fingerprint is *unavailable*
(`no_trusted_git`), never run unsafely. The trust assumption is stated, not removed: you
trust the executables found in your own `PATH` outside the project.

**What git does once it runs.** A reviewed repository can select executable helpers through
`.gitattributes` and its config (`textconv`, clean filters, `core.fsmonitor`, external diff).
All host-side `git` goes through `bridge/gitsafe.py`, which uses:

`--no-optional-locks --attr-source=<empty tree> -c core.fsmonitor=false
-c core.attributesFile=<devnull> -c diff.external= -c core.pager=cat -C <root>` plus
`--no-textconv --no-ext-diff` on diff commands, with a scrubbed environment
(every inherited `GIT_*` variable is dropped, matched case-insensitively, then only the controls
above and `NoDefaultCurrentDirectoryInExePath` are set) and a minimum git version (2.40, which introduced `--attr-source`; older git is reported as
`git_too_old_for_safe_mode` and the fingerprint is unavailable — git is never run unsafely).

A control test builds a repository whose worktree `.gitattributes` and `.git/config` select
a textconv driver, a clean filter and an fsmonitor hook; it requires that plain `git` runs
them (a control that does not fire invalidates the test), and that **nothing** runs through
the bridge's fingerprint or preflight-file selection. A second test re-runs that control in a
child interpreter with the flags removed and requires it to fail (so the control cannot pass
for the wrong reason). Separately, a recursive before/after snapshot of `.git` is unchanged by
the fingerprint. `--no-optional-locks` alone is not claimed to prevent every git-related
write.

**Not covered:** attributes supplied from inside `$GIT_DIR` (git still reads
`info/attributes`; a repository directory whose `.git` an attacker can already write is
outside this threat model), and a git bug that executes code regardless of these controls.

## Supported baseline

Only Codex CLI **0.162.1** has test evidence. The gate is an *exact* match: a sibling patch
release is not presumed equivalent (flags, event shapes, sandbox helpers and gates may differ).
`GPT_ARCHITECT_ALLOW_UNTESTED_CLI=1` is a human opt-in that is recorded in telemetry and
appended to every answer. The bridge never upgrades, downgrades or reconfigures the CLI.
Platform: Windows 11, Python 3.13.

## Install (clean) and rollback

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --no-deps -r requirements.lock.txt
.\.venv\Scripts\python.exe tools\verify_lock.py        # must print "environment matches"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
```

Rollback of the install = delete `C:\AI\gpt-claude-mcp-bridge-v2` and, optionally,
`%USERPROFILE%\.gpt-architect-bridge-v2`. v1 is never involved.

## Configuration

Set in the **MCP server's environment**. Precedence for model/effort (per tool, each
independently): per-tool variable → global variable → built-in default. Empty = unset.

| Variable | Default | Meaning |
|---|---|---|
| `GPT_ARCHITECT_MODEL` / `_EFFORT` | `gpt-6.1-sol` / `high` | model / effort for both tools (v1 compatible) |
| `GPT_ARCHITECT_MODEL_CONSULT` / `_REVIEW`, `GPT_ARCHITECT_EFFORT_CONSULT` / `_REVIEW` | – | per-tool overrides, validated against `codex debug models` before inference |
| `GPT_ARCHITECT_TIMEOUT` | `900` | seconds for one turn (1–86400) |
| `GPT_ARCHITECT_QUEUE_TIMEOUT` | timeout + 60 | wait for another call on the same repo/model/session |
| `GPT_ARCHITECT_SESSION` | `default` | named session for this server process |
| `GPT_ARCHITECT_MCP_ALLOW` | – (empty) | MCP servers allowed to stay enabled; **not contained** (see above) |
| `GPT_ARCHITECT_ON_TURN_INFRA_FAILURE` | `error` | accepts only `error` (an answer whose actual turn hit infrastructure failures is withheld). The former `degraded` mode was **removed**; any other value is rejected as `invalid_configuration/bad_env` so an old setting fails loudly instead of changing meaning |
| `GPT_ARCHITECT_SANDBOX_PREFLIGHT` | `1` | `0` skips the readiness preflight (not recommended; recorded) |
| `GPT_ARCHITECT_WINDOWS_SANDBOX` | unset | `elevated`/`unelevated`; unset = your Codex config decides. Changes the sandbox implementation; an override is a configuration without attack-test evidence and is bannered in every answer and recorded in telemetry |
| `GPT_ARCHITECT_ALLOW_UNTESTED_CLI` | `0` | `1` = run with an unsupported CLI version (recorded, bannered) |
| `GPT_ARCHITECT_V2_STATE_DIR` | `~/.gpt-architect-bridge-v2` | state (outside the repo, separate from v1); validated before **every** state access, including registry-wide admin commands and `status` |
| `GPT_ARCHITECT_CODEX_PATH` | PATH lookup | explicit launcher (advanced / tests) |
| `CLAUDE_PROJECT_DIR` | cwd | repository root (must contain `.git`) |

## Authentication

`codex login status` is classified every call (ChatGPT required; missing / API-key /
unsupported refused with actionable text). The bridge never logs in or out, never reads
`auth.json`, and removes `CODEX_API_KEY` / `OPENAI_API_KEY` from every child process.

## Sandbox preflight is readiness, not proof — and the actual turn is inspected

*Before* inference the bridge runs three model-free `codex sandbox` stages with the call's
own overrides and cwd: (1) a process can start (`cmd /c cd`); (2) `git rev-parse
--is-inside-work-tree`; (3) a small tracked file can be read. Failure is
`sandbox_unavailable` (`sandbox_setup_failed`, `sandbox_timeout`, `repo_unreadable`,
`source_file_unreadable`) with no allowance used. Limits, stated plainly:

* stage 3 is a **tracked-file readability probe**, not proof that source files in general are
  readable. The file is chosen host-side (preferring a small file with a source extension);
  names containing characters `cmd.exe` would reparse (`& | < > ^ % ! " ' ( ) [ ] { } ; , = $`,
  whitespace, a leading `-` or `/`) are never selected, and the name reaches the sandbox as
  *data* to `findstr`, never as `cmd /c type <name>`;
* if no safe small file exists the stage is **skipped visibly**: the answer carries a
  `[bridge-warning]` and telemetry records `sandbox_preflight_source_read_skipped:<reason>`;
* `codex sandbox` is not the same code path as `codex exec`; preflight and the real turn can
  differ, and state can change between them (the ChatGPT app can start `node_repl.exe` in
  that window);
* a disabled (`GPT_ARCHITECT_SANDBOX_PREFLIGHT=0`) or skipped preflight is bannered in the
  answer, not only recorded.

Therefore the **actual turn** is inspected. Evidence considered:

* completed command/tool items that *report failure* (status failed / non-zero exit), over
  their **full** captured output, including arbitrarily nested result/content containers; only
  keys that carry what was *asked* of the tool (`command`, `input`, `cwd`, ...) are skipped, so
  the words in a `git grep helper_unknown_error` command are not a failure;
* top-level `error` events that arrive before completion;
* lines too large to parse are counted (`events.oversize_lines`) and the WHOLE line is streamed
  through the signature scan (a signature anywhere in it is found, including the middle); the
  item type is read from the retained head, and an oversized record recognised as agent
  narration/reasoning is excluded like a parsed prose item. A record of unknown type carrying
  a signature is counted conservatively (a >1 MiB line that merely contains the words is a
  false positive we accept) (`infra.oversize_signature_lines`).

A signature of sandbox setup (`helper_unknown_error`, `setup refresh had errors`, …),
process creation (`Failed to create unified exec process`, …) or repository access
(`fatal: not a git repository`, unreadable tree/object, permission denied, in the repository
cwd) makes the call fail with `sandbox_unavailable/turn_*` and the answer is **withheld**,
even if the turn "completed" with a plausible answer. Agent narration is never scanned, and
ordinary non-zero exits (`git diff` → 1, `grep` without matches) are not infrastructure
failures. The bridge never infers successful repository inspection from model narration. An
unseen item shape degrades to "not flagged" (see *Known limitations*).

## Process ownership and termination (F3, N3, N4)

The Codex child is started **suspended**, placed in a **Windows Job Object**
(`KILL_ON_JOB_CLOSE`, no breakaway), and only then resumed (`NtResumeProcess`), so every
descendant is a job member from its first instruction:

* if ownership cannot be established the child is killed **before any prompt byte is written**
  and the call fails (`subprocess_failed/ownership_unavailable`, history unaffected); the old
  "ran anyway, normal exit looks fine" path no longer exists. A failure to start the CLI at
  all (`subprocess_failed/spawn_failed`) likewise ran nothing: it does not block the session;
* the whole lifecycle after process creation is exception-safe, including building the helper
  objects and starting the reader/writer threads: a setup failure terminates every owned
  process (even when the root already exited and only a descendant is left), releases the job
  handle in an outermost `finally`, joins only threads that actually started, keeps the original
  categorised error, leaves an `uncertain` in-flight marker (`runner_failed`) and blocks the
  session. Reader threads start first and the prompt writer last, so a failed start can never
  have written a prompt byte;
* the job's live-process count makes termination **confirmed** (0 members) rather than
  attempted; messages say `CONFIRMED` or `ATTEMPTED but NOT CONFIRMED`. After a normal exit
  the count lags process teardown by milliseconds (measured ≈4 ms for a launcher +
  interpreter pair), so a member is called a surviving descendant (`reaped_descendants`) only
  if it is still present after a 0.5 s grace window; survivors are then terminated and
  reported;
* **P2 (conservative default):** commands launched by the Windows sandbox *service*
  (`codex-windows-sandbox-service.exe`) are outside the job and unobservable. After a
  timeout or cancellation on Windows the session is therefore marked **in doubt**
  (`service_children_unobservable`) and refused until the operator runs
  `bridge_admin.py reconcile --yes`. Recovery never kills anything;
* **a stale in-flight marker is never auto-recovered.** A marker whose recorded owner (PID
  *and* process creation time, so PID reuse does not matter) is alive stays in doubt
  (`stale_inflight_owner_alive`); an owner that exists but cannot be queried (e.g. access
  denied) counts as alive, and only a PID that verifiably does not exist or has exited counts
  as dead. A **dead** owner no longer means "recoverable": closing the job proves only that
  the job's *members* are gone, commands started by the sandbox service are outside it, and
  nothing durable proves the turn did not complete. The marker therefore becomes `uncertain`
  (`bridge_terminated`) and the session needs `reconcile --yes`; a completed-but-unbound turn
  becomes a pending-persistence state (below), and the replacement record is written *before*
  the old one is removed, so a failed write keeps the session blocked rather than silently
  recoverable.

**Limits:** service-spawned commands can neither be enumerated nor confirmed terminated by
this mechanism (P2 above makes the operator the confirming party); nested-job assignment can
fail on old Windows (ownership is then unavailable and the call is refused).

## Sessions, registry lifecycle and recovery (F4, F5, N5, N6, N10)

State directory: `instance.json`, `registry.json` (+ `registry.prev.json`), `locks/`,
`inflight/`, `pending/`, `journal.jsonl` (content-free), `maintenance.json`, `telemetry/`.

* **Admission is durable.** The in-flight marker must be written before inference
  (`state_error/inflight_marker_unwritable` refuses otherwise; no turn is started). It is
  retained, in phase `completed_unbound`, until the pointer is committed — or a pending or
  uncertain marker has taken its place — so a crash between "answer produced" and "pointer
  saved" leaves a blocking state (`pending_persistence`) rather than a silently replaceable
  session. If neither the pointer nor the pending marker can be written the retained
  in-flight marker keeps the session blocked (and is moved into a pending marker as soon as
  that becomes possible). A bridge death at *any* point of a turn, including before the
  completed phase was written, is treated as in doubt (above).
* **Process uncertainty and thread disposition are different facts with different
  resolutions.** `reconcile --yes` confirms that no process of the interrupted call remains;
  it does not decide what happens to a Codex thread the call produced. A fresh call that
  observed its thread leaves a separate pending marker (or an `unbound_thread` on the
  in-flight record when that could not be written), and `reconcile` refuses to discard an
  unsaved-thread record (`persistence_failed`). `adopt --thread <id>` keeps the thread and
  `reset` discards the pointer state; each settles only the thread debt and leaves an
  independent process uncertainty in place until `reconcile`.
* **Instance identity fails closed.** `instance.json` is `absent` / `unreadable` / `invalid` /
  `ok` (`registry_established`). A missing registry on an *established* instance is
  `session_corrupt/registry_lost`; an unreadable or malformed instance file
  (`instance_unreadable`, `instance_invalid`) or a registry without a string identity
  (`registry_identity_unverifiable`) is never treated as first initialisation, and a foreign
  `instance_id` is refused. `restore-prev` validates the backup's schema and identity before
  replacing anything.
* **Maintenance is exclusive.** Registry-wide commands (`quarantine`, `reinit`,
  `restore-prev`) hold an OS lock plus an owner-stamped flag for their whole lifecycle; a
  second maintenance owner fails (`maintenance_busy`), only the owner clears its flag, and a
  **stale flag is never overwritten**: a new maintenance command refuses
  (`maintenance_stale`) until `end-maintenance --yes` clears it. The lock is released on every
  path (a failing unlink or quiescence probe does not leak it). New calls and the session commands
  (`new`, `reset`, `adopt`, `reconcile`) refuse while maintenance is active
  (`state_maintenance`). Quiescence uses non-blocking session-lock probes; a late
  `bind_thread` against a lost registry fails instead of repopulating it.
* **State directory validation applies to every admin command** (`state_in_repo`,
  `state_overlaps_v1`): a rejected directory is never created, locked or probed — including by
  `status`.
* **Reset retires bridge pointers only.** Codex history is never deleted or modified.

```powershell
bridge_admin.py status [--repo P] [--all] [--json] [--sandbox]
bridge_admin.py new|reset|adopt|reconcile ...   # per session; reset/adopt/reconcile need --yes
bridge_admin.py quarantine|reinit|restore-prev|end-maintenance --yes   # registry-wide
```

### Partial and uncertain outcomes

The bridge *pointer*, Codex *history* and process state are different things; each is
reported separately and the bridge never claims history is unchanged merely because the
registry was not updated. Independent facts are listed together, **before** the answer: when
any applies the result starts with `[bridge-warning] N warning(s) accompany this answer:`, then
one `[bridge-warning] n. ...` line per fact, a blank line, and only then the answer (a long
answer can be truncated by the consumer; a leading block cannot be missed). No recorded
warning is telemetry-only: a warning without its own caller-visible text is rendered
generically (`Recorded warning: <code>.`).

| Outcome | Returned | Pointer | Next call |
|---|---|---|---|
| `success` | answer (+ warning block when one applies, e.g. repository changed, allow-listed MCP, disabled/skipped preflight, unavailable consistency or post-turn MCP check, reaped descendants) | bound | normal |
| `success_partial_persistence` | answer + PARTIAL SUCCESS banner with the thread id | **not saved**; pending marker (or retained in-flight marker) | refused (`pending_persistence`) until `adopt` or `reset` |
| `success_session_in_doubt` | answer + in-doubt banner | bound | refused (`uncertain_termination`) until `reconcile` |
| timeout / cancelled / error after inference started | error naming the thread id (if observed and well-formed) | unchanged | refused after a Windows timeout/cancel until `reconcile` (P2); otherwise normal |
| infrastructure failure in the actual turn | `sandbox_unavailable/turn_*`, **answer withheld** | unchanged | normal |

There is no degraded success: an answer is either returned as a review (with whatever flags
apply) or withheld. Errors after inference started always say *"Codex history MAY contain a
partial or complete turn in thread …"*; only a call that never started a turn says history is
unaffected. Nothing is ever replayed. A "resumed, pointer already references this thread"
statement is made only when a fresh read of the registry proves it.

## Locks: what they coordinate (and what they do not)

Session and registry locks are advisory OS file locks that coordinate **cooperating
processes sharing this v2 state directory**. They do not cover v1 (separate state), a manual
`codex resume` of the same thread, the Codex desktop app, or any other writer. Lock order is
fixed: session lock → registry lock (brief) → journal/telemetry lock (leaf); admin commands
use maintenance lock → quiescence probes → registry lock and never take a session lock while
holding the registry lock. **Session locks do not freeze the repository** (next section).

## Review consistency (F6, N11, N15)

Before and after every turn the bridge fingerprints the repository through the contained git
described above: `HEAD` (`UNBORN` only when *verified*: HEAD is a symbolic ref whose target ref
does not exist; an incomplete or failed HEAD read makes the fingerprint unavailable),
`git status --porcelain=v1 -z --untracked-files=all`
(capped at 8 MiB), **separately** streamed hashes of the staged diff (`--cached`) and the
worktree diff, and size+mtime of untracked paths. Staged-only changes with unchanged status
and worktree bytes, and tracked changes in an unborn repository, therefore change the
fingerprint. A truncated status, a reader that died mid-stream, a failed diff or a timeout makes
the fingerprint *unavailable*, never comparable.

A change during the review is reported in the answer (`[bridge-warning]`), in telemetry
(`repo.changed`, digest prefixes only) and in warnings; no paths, diffs or source enter
telemetry. An **unavailable** fingerprint is reported in the answer too ("Repository
consistency could NOT be checked (reason)"), not only in telemetry. Known blind spot:
same-size/same-mtime edits to *untracked* files.

**Side-by-side evaluation:** the MCP tool names are identical in v1 and v2, so identify the
selected server explicitly (the server name you registered, e.g. `gpt-architect-v2`) and
**pause implementation edits while a review runs**, or the fingerprint will (correctly)
report the change.

## Error categories

`[bridge-error:<category>/<code>] message` + `Recovery:`. Categories: `invalid_input`,
`invalid_configuration`, `cli_missing`, `cli_incompatible`, `authentication`,
`capability_audit_failed`, `state_error`, `session_corrupt`, `session_mismatch`,
`session_in_doubt`, `queue_timeout`, `rate_limited`, `subprocess_failed`, `timeout`,
`cancelled`, `output_incomplete`, `sandbox_unavailable`, `internal_error`. Successful calls
return plain answer text (plus `[bridge-warning]` lines when something needs attention).

Error text may include a short diagnostic excerpt (CLI stderr tail, preflight output, an
event message). Excerpts pass through `bridge/redact.py`, which is **best-effort redaction of
secret-shaped tokens only** (`sk-…` keys, bearer tokens, JWTs). It does not remove paths or
source fragments, so an excerpt can contain them. Excerpts go to the caller in the error
text; they are never written to telemetry (which carries codes and counters only).

## Telemetry

`<state dir>\telemetry\calls.jsonl` (rotating, cross-process safe), one terminal record per
invocation, redacted by construction: ids, route, session/thread, durations, outcome, error
category/code, usage (`null` when not reported), event/infra counters, capability evidence
summary, process ownership/termination, repository digests (prefixes), pointer/history/session
state, prompt/answer lengths. Never prompt text, source, answers, credentials, error prose or
repository paths. Identifiers are validated **at ingress** (a malformed `thread_id` is dropped
and reported as `output_incomplete/bad_thread_id` without being echoed, journalled or
persisted); identifier formats are matched in full (a trailing newline is not accepted);
typed top-level fields (`outcome`, `tool`, `error_category`, `pointer_state`, `history`,
`session_state`, `call_id`, `error_code`, `cli_version`) are checked against their own enum or
format and dropped to `null` otherwise; and every nested string must be an opaque token or a
fixed-format value (otherwise it is replaced by `<invalid>`). Every telemetry destination is
validated like the state directory, including the fallback; when no safe destination exists
the record is skipped with a content-free stderr line, never written inside the repository.
Examples:
`docs/telemetry_examples_remediation.jsonl` (fake CLI; fixture values).

## Tests and checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v      # no subscription use
.\.venv\Scripts\python.exe tools\record_evidence.py tests --label <name>  # bind result to source hash
.\.venv\Scripts\python.exe tools\record_evidence.py integrity               # retain v1/lock check output
.\.venv\Scripts\python.exe tools\record_evidence.py cli [--rollout PATH]     # model-free CLI observations
.\.venv\Scripts\python.exe tools\v1_manifest.py compare docs\v1_baseline_before.json
.\.venv\Scripts\python.exe -B tools\make_verification_prompt.py            # prepares (never sends) the reviewer prompt
```

Evidence that is retained or embedded in a prompt for a third party carries no local
identifiers: the CLI observations keep public help/schema text and normalised MCP
`(name, enabled)` lists only (never transports, commands or environments, and the login line
is stored as a classification), and `make_verification_prompt.py` replaces the machine's
profile path and account name with `<home>` / `<user>` in everything it embeds (flagged per
file in the manifest).

A test record is valid only if the source tree was byte-identical **before and after** the run
(both fingerprints are taken; a difference marks it `INVALID`), the process exited 0 with zero
skipped tests, and the raw output is retained (`tests_<label>.log`, hash in the record).
`make_verification_prompt.py` imports nothing that can start a process or open a connection
(checked by a test) and refuses to build unless every evidence record is bound to the current
source hash, was not changed during its run, and records a passing, skip-free run with an
intact retained log.

Proposed **opt-in live** checks (consume allowance; NOT executed in either remediation pass):
`tools\live_smoke_test.py` and `tools\apply_patch_attack_test.py --live` (see
`REVIEW_PACKAGE.md` §10). The attack oracle is **wrapper-attested**: Codex persists no nested
`apply_patch` records, only the model's `exec` wrapper and its output. A wrapper is accepted
only inside a **strict pass-through grammar** — optional string-literal bindings, exactly one
`await tools.apply_patch(<literal | bound literal | {input: ...}>)`, and `text(result)` (or
`text(await ...)`) printing that call's own result — and the patch is decoded from the call's
*argument*, never searched for in the source. Comments, interpolation, control flow, other
calls, unused literals, shadowed names and runtime-assembled strings are outside the grammar
and make the attempt INCONCLUSIVE. A PASS then means: that single call received exactly the
expected patch, its output carried a *specific* policy rejection (or an OS denial of a
**write** of that very target — an unrelated target or a read denial is not evidence), counts
and thread identity matched per turn, and the filesystem was unchanged. It is not an
independent observation of the nested call; generic write failures (e.g. disk full) are never
conclusive; a wrapper shape outside the grammar is INCONCLUSIVE, not a pass; and a timeout or
launch failure is inconclusive. The live stage tests the **production argv + runner**, not the
MCP stdio / `ArchitectService` path.

## Activation (human decision) and rollback — NOT EXECUTED

See `REVIEW_PACKAGE.md` §10. v2 is not registered. Side-by-side registration under a distinct
name (`gpt-architect-v2`) is the recommended first step; cut-over and rollback commands are
listed there. Registering or removing a server does not touch Codex history; *evaluation
calls* do add Codex threads. Keep the ChatGPT/Codex desktop apps closed while the bridge
runs (the Windows sandbox refresh fails while their `node_repl.exe` is alive).

## Known limitations

* Windows 11 / Python 3.13 / Codex 0.162.1 only; the lock is `win_amd64`-specific.
* `web_search` and runtime tool removal for delegation/image generation are **not** verified
  (no CLI readout); web exposure (`web__run`) is an open human decision.
* MCP containment is a configuration snapshot plus a narrowed, not eliminated, race.
* Termination of commands started by the Windows sandbox service cannot be confirmed; the
  conservative default requires operator reconciliation after a timeout/cancel.
* Sandbox preflight is readiness only; the elevated Windows sandbox refresh fails while a
  `node_repl.exe` runs, and a lock appearing between preflight and the turn is detected only
  by the actual-turn inspection.
* Actual-turn failure detection keys on item shapes observed in Codex's session record; an
  unseen item shape degrades to "not flagged" (a false negative), never to a pass of the
  attack tests. Live `--json` shapes for failed commands were not re-captured.
* The attack oracle is wrapper-attested (above), and its strict grammar means an unusual but
  genuine wrapper shape is reported INCONCLUSIVE rather than guessed at.
* Executables are taken from your own `PATH` outside the project; a compromised `PATH` entry
  is outside the threat model. Tools in `tools/` use the same resolver for disposable repos.
* The oversized-line scan is conservative: a >1 MiB line of unknown type that contains a
  signature is counted even if it was not a failure.
* A failed first turn leaves a thread in Codex history; it is journalled, not deleted.
* Rate-limit/auth/session-missing detection is message-pattern based.
* Hook discovery is a heuristic over `CODEX_HOME` and `<repo>/.codex`; hooks are controlled
  by the `hooks` gate (resolved), not by discovery.
* Single-user design; named sessions are selected per MCP server process, not per call.
