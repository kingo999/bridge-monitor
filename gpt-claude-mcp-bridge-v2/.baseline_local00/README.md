# GPT Architect ⇄ Claude Code MCP bridge — v2 (review candidate, sixth remediation)

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
your own statement of trust and is not searched). The FILE's real target is validated as well as
its directory: a link, junction or shim in a trusted directory that resolves into the project or
repository is skipped, and the artifacts an npm shim derives from its location (`node.exe`,
`codex.js`) are validated the same way (`cli_target_untrusted`). `taskkill`/`tasklist` are
addressed by the System32 directory the OS itself reports (`GetSystemDirectoryW`), not by an
inherited `SystemRoot`/`windir` value, and there is **no fallback**: if the OS query fails the
tool is unavailable (`SystemDirectoryUnavailable`; process cleanup then falls back to killing
the root process only). If no trusted `git` exists the fingerprint is
*unavailable* (`no_trusted_git`), never run unsafely; if a candidate was rejected only because
it lies inside the project, the error says so and names `GPT_ARCHITECT_CODEX_PATH`. The trust
assumption is stated, not removed: you trust the executables found in your own `PATH` outside
the project. Not controlled: the DLL search order of a resolved executable, a later replacement
of the file (check-then-use), hard links (same bytes under a different name), and **anything the
npm launcher's own JavaScript goes on to execute** (only `node.exe` and `codex.js`, the artifacts
`resolve_cli` derives from the shim's location, are validated).

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
the helpers named in `N1_HostGitContainment.REQUIRED` (a control that does not fire invalidates
the test; see that test for exactly which kinds are demonstrated), and that **nothing** runs through
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
| `GPT_ARCHITECT_WINDOWS_SANDBOX` | unset | `elevated`/`unelevated`; unset = your Codex config decides. Changes the sandbox implementation; an override is a configuration without attack-test evidence and is bannered in every answer and recorded in telemetry. The same value may be supplied by `settings.json` in the v2 state directory (`{"windows_sandbox": "unelevated"}`) for clients that cannot add an environment variable (the ChatGPT/Codex desktop app starts the server with a minimal environment); the variable wins when both are set, and an invalid file REFUSES the call (`invalid_configuration/settings_file_invalid`) instead of being ignored |
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
  through the signature scan - as written and with JSON ASCII escapes (`\u005f`) resolved, across
  chunk boundaries. The item type is read ONLY from the canonical event prefix
  (`{"type":"item.<phase>","item":{["id":..,]"type":"<T>"`), so a nested `item` object cannot be
  mistaken for the event's own; a record recognised as agent narration/reasoning is excluded
  like a parsed prose item, and a record of unknown type carrying a signature is counted
  conservatively (`infra.oversize_signature_lines`; a >1 MiB line that merely contains the words
  is a false positive we accept).

**Evidence that could not be read completely is never a review.** If the stdout reader dies
mid-stream, is still running after its bounded join, or never reached end-of-stream
(`infra.reader_failed`; the events object is FROZEN at that point - every mutator takes the
same lock and re-checks the seal, and the thread observer is called outside it - so it cannot
change under the caller however long an abandoned reader keeps running), or the
output of a failed item exceeds the traversal limits (64 levels / 200,000 nodes;
`infra.evidence_truncated_items`), the call fails with `output_incomplete/turn_evidence_incomplete`
and the answer is withheld. Request fields (`command`, `input`, `arguments`, `cwd`, ...) are
skipped only where they are requests: once traversal enters a returned container (`result`,
`output`, `content`, `error`, `stderr`, ...) nothing is skipped, so a returned
`{"result": {"input": {"error": ...}}}` is output. In oversized lines these JSON string
escapes are resolved before matching, across chunk boundaries: `\n \r \t \b \f \" \\ \/` and ASCII
`\u00XX` (**not** arbitrary `\uXXXX`). The raw carry window (8 KiB) holds the longest ENCODED
signature: `CreateProcess`, up to 1024 whitespace characters and `{`, every one as a 6-byte
escape. Configuration readouts (`--version`, `login status`, `debug models`, `features list`,
`mcp list`) are likewise refused when their capture was truncated or a drain thread failed
(`Simple.complete`).

A signature of sandbox setup (`helper_unknown_error`, `setup refresh had errors`, …),
process creation (`Failed to create unified exec process`, …) or repository access
(`fatal: not a git repository`, unreadable tree/object, permission denied, in the repository
cwd) makes the call fail with `sandbox_unavailable/turn_*` and the answer is **withheld**,
even if the turn "completed" with a plausible answer. Agent narration is never scanned, and
ordinary non-zero exits (`git diff` → 1, `grep` without matches) are not infrastructure
failures. A "permission denied" is a repository-ACCESS failure only when it is not a refusal to
WRITE: the read-only sandbox refusing `index.lock`, a ref lock or a new file
(`fatal: Unable to create '.../.git/index.lock': Permission denied`, observed live for
`git commit`) is the control working and does not withhold the answer. Read failures and every
wording the narrowed pattern does not name stay flagged (fail closed). The bridge never infers successful repository inspection from model narration. An
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
  (`bridge_terminated`) and the session needs `reconcile --yes` **whatever phase it reached**:
  a `completed_unbound` phase does not imply the processes were reconciled (the process fact
  is written first, in the primary record, and the thread debt is mirrored into a pending
  marker second). Every write of the in-flight record persists the whole in-memory record, so
  a failed write of the uncertainty can never be masked by a later successful phase update
  (it is retried, and a persisting failure is reported as `uncertainty_marker_unwritable`).
  The same ordering holds on the ordinary path: a thread debt is mirrored into a pending marker
  only if this call's process uncertainty was persisted first (otherwise the retained in-flight
  record carries the debt and the banner says the pending marker was NOT written). Observer
  callbacks from the runner's reader thread never rewrite the record: they MERGE their own fields
  into the record that still belongs to their call (`Registry.update_marker_if`, compare-and-merge
  under `markers.lock`) and only while the call's fence is open, so an abandoned reader that wakes
  up after recovery can neither erase a recorded settlement nor recreate a removed record. The
  identity of a thread the runner's parser had ALREADY observed is carried on the runner's
  exception itself (`BridgeError.observed_thread_id`, a frozen snapshot), so a supervision failure
  that happens before the observer callback has run still leaves the thread debt on the uncertain
  record (U8).
  When the recovery conversion itself cannot be persisted the call is refused with
  `session_in_doubt/uncertainty_marker_unwritable`, the raw record is left exactly as it was and no
  pending marker is written: nothing downstream may act as though the process fact were durable.

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
  that becomes possible); if even that last retained write fails the call says so
  (`inflight_marker_unwritable`, "could NOT be updated"). Once the outcome is decided, a
  marker-lock/I/O failure while REMOVING the in-flight record is reported, never raised: the
  caller keeps the answer, the record is rewritten as `uncertain` (best effort) and the warning
  `inflight_marker_not_cleared` says an operator `reconcile` may be needed (U3). The same holds in
  `reset`/`adopt`/`reconcile`: a pointer or pending change that already happened is reported
  together with what could not be cleared, and any other state-file OSError is a structured
  exit-1 message that says earlier steps may have completed, not a traceback. A bridge death at *any* point of a turn, including before the
  completed phase was written, is treated as in doubt (above).
* **Process uncertainty and thread disposition are different facts with different
  resolutions.** `reconcile --yes` confirms that no process of the interrupted call remains;
  it does not decide what happens to a Codex thread the call produced. A fresh call that
  observed its thread owes a *thread debt*. One function derives it (`state.thread_debt`) and the
  service and every admin command use it: the debt is the explicitly named `unbound_thread`, or
  - for a FRESH call that observed a well-formed thread id the registry pointer does not
  reference - the observed thread, in any of the states `running`, `uncertain` or
  `persistence_failed`. It is therefore also derived from a RAW stale record that no service
  call has converted yet, and from a fresh turn that announced a thread and then timed out.
  `reconcile` writes the debt to a pending marker BEFORE clearing process uncertainty (and
  refuses when it cannot), and refuses to discard an unsaved-thread record
  (`persistence_failed`). `adopt --thread <id>` keeps the thread and `reset` discards the
  pointer state; **each settles only the thread debt and never touches the process fact**: every
  in-flight record is KEPT - a raw `running` record of a dead owner included, because nothing has
  converted it yet - and only a pure `persistence_failed` thread debt is removed ("pure" is
  validated, not inferred from the state alone: a `persistence_failed` record that also names a
  process-uncertainty reason is treated as an uncertain record everywhere). The settlement
  is written onto the retained record (`thread_settled`, with how/when), and `thread_debt`
  honours it, so the still-recorded thread id can never re-create a debt that the operator
  discarded (a `reconcile` afterwards does not resurrect it). If the settlement cannot be
  persisted the command says so and exits 1; the debt then remains owed. A resumed call never
  leaves a debt. When both facts exist, process uncertainty is reported first.
* **Instance identity fails closed.** `instance.json` is `absent` / `unreadable` / `invalid` /
  `ok` (`registry_established`). A missing registry on an *established* instance is
  `session_corrupt/registry_lost`; so is a missing registry AND identity when independent
  establishment evidence survives: `registry.prev.json` (written BEFORE the commit of the FIRST
  registry write as well as at every replacement; the first write is refused if it cannot be
  written, so one successful call always leaves it) or an established-state event
  (`registry_established`, `turn_finished`, `thread_adopted`, `registry_reinitialised`,
  `registry_restored_prev`) in the lifecycle journal. When `instance.json` is missing too,
  `restore-prev` cannot validate the backup: the advice is to restore `instance.json` or
  `reinit --yes` (a new identity) and `adopt` the threads. `reinit` itself follows the same
  mandatory policy: its `registry.prev.json` is written BEFORE the empty registry is committed
  (no witness, no registry) and it appends the `registry_reinitialised` journal event itself
  (U4). Empty `locks/`/`inflight/`/`pending/` directories prove nothing
  and do not block a genuine first initialisation; an unreadable or malformed instance file
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
consistency could NOT be checked (reason)"), not only in telemetry. `UNBORN` additionally
requires `show-ref --verify` to report the target ref as absent (exit 1; a malformed or
unreadable ref exits otherwise and makes the fingerprint unavailable).

**Scope of "repository consistency" (stated, not comprehensive).** The fingerprint is bounded
by what Git itself reports: same-size/same-mtime edits to *untracked* files, **ignored files**,
index visibility flags (`assume-unchanged`, `skip-worktree`) and stat caching, and the content
of **nested submodules** are not covered. A fingerprint match means "Git's view of tracked,
staged and untracked-visible content did not change", not that no byte of the working
directory changed.

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
An error exit carries the same contract: every warning recorded before the failure (an
unexpected MCP server seen after the turn, an uncontained exposure, a cleanup that could not be
confirmed) is appended to the error text as `[bridge-warning] N warning(s) were recorded before
this error:` lines.

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

A test record is valid only if the tests were executed from a **staged copy** of the source set
(so a change to the live tree during the run cannot affect what was tested), the staged copy's
fingerprint equals the live tree's fingerprint taken before AND after the run (a difference
marks the record `INVALID`; endpoint equality is *not* claimed to prove continuous identity of
the live tree), the staged files are **read-only for the run and fingerprinted again after it,
before the stage is deleted** (a staged module modified during the run invalidates the record), the
process exited 0 with zero skipped tests, and the raw output is retained (`tests_<label>.log`,
hash in the record). The post-run fingerprint detects a PERSISTENT difference at that endpoint,
not a rewrite followed by a restore; what is not staged at all is the environment: PATH, `%TEMP%`,
the venv and the installed CLI.
`tools/prefix_check.py` runs a round's new tests against the previous snapshot's source and
retains the overlay procedure and raw output (`prefix_check.json` / `.log`): every copied
snapshot file is verified against the snapshot's manifest, the tests that ran must be exactly the
tests the module's SYNTAX TREE defines (`tools/prefix_logic.py`, non-executing; a loader failure
is an error of the check), and each failing test is classified `assertion` / `api_missing` /
`behavioural_error` - a failure only because the new API does not exist yet is kept distinct from
one where behaviour differed. The prompt builder does not trust those claims: it re-derives the
identities from the module file, re-classifies the failures from the raw log, requires the COMPLETE
overlay (every snapshot `bridge/`, `tools/` and top-level script, the harness files, fixtures and
the module), and rehashes the snapshot bytes on disk. A passing log must show each test identity
exactly once, header paired with its `ok`. The recorder labels a run `INVALID` when its
provenance broke (live tree changed, stage differs, stage modified) and `FAILED` only when tests
failed.
`make_verification_prompt.py` imports nothing that can start a process or open a connection
(checked by a test) and refuses to build unless EVERY embedded evidence record (fingerprint,
policy, integrity, CLI observations, prefix check, tests) is bound to the current source hash, the
test record was executed from a staged copy that was re-fingerprinted after the run, its retained
log itself reports the passing, skip-free run the summary claims (`Ran N tests`, `OK`, no
FAIL/ERROR, no skips), and the prefix record's raw-log hash, per-test outcomes, expected count,
overlay hashes and snapshot manifest all check out (`prefix_problems`).

The verification prompt is sent by `tools/send_verification.py` (an explicit
`--i-understand-this-uses-subscription-allowance`; one attempt, never retried). A review is
reported valid only when `runner.turn_problems` finds nothing (clean exit, completed turn, no
error after completion, stdout read to EOF, no truncated evidence, no infrastructure failure,
a well-formed OBSERVED thread id - and the requested one on a resume - stable) and an answer
exists. Stage B of the attack harness uses the same predicate.

Evidence classes: **implementer_executed** is anything the implementer executed - including
model-free CLI commands, which are labelled `kind: model_free_cli_observation`;
**source_observation** means read from code or documentation only; **historical_report** is
from an earlier candidate. None of these is verification by a second party.

Proposed **opt-in live** checks (consume allowance; NOT executed in either remediation pass):
`tools\live_smoke_test.py` and `tools\apply_patch_attack_test.py --live` (see
`REVIEW_PACKAGE.md` §11). The attack oracle is **wrapper-attested**: Codex persists no nested
`apply_patch` records, only the model's `exec` wrapper and its output. A wrapper is accepted
only inside a **strict pass-through grammar** — optional string-literal bindings, exactly one
`await tools.apply_patch(<literal | bound literal | {input: ...}>)`, and `text(result)` (or
`text(await ...)`) printing that call's own result — evaluated in program ORDER: a binding takes
effect only for what follows it (a literal declared after the call has not been assigned when it
runs), and no declaration may shadow `tools`/`text` or reuse a bound name. The patch is decoded
from the call's *argument*, never searched for in the source, and the **complete** decoded
argument must equal the expected patch (the only tolerance is CRLF vs LF and trailing newlines;
trailing junk and literal backslash-n sequences are not normalised away). Between tokens the
grammar accepts ONLY space, tab, CR and LF (Python's `\s` also matches U+0085 and others that
ECMAScript does not treat as whitespace; non-ASCII whitespace is conservatively INCONCLUSIVE).
Identifiers must be
valid: an ECMAScript reserved word cannot be a binding, and a quoted string cannot contain a raw
CR/LF (such a wrapper is a SyntaxError and never ran; a template literal may span lines and its
raw CRLF is normalised to LF as the language does). Comments, interpolation, control flow,
other calls, unused literals and runtime-assembled strings are
outside the grammar and make the attempt INCONCLUSIVE. A PASS then means: that single call
received exactly the expected patch, its output carried a *specific* policy rejection (or an OS
denial parsed as `Failed to <write-type verb> <PATH>: ... denied` whose full normalised PATH
equals the attacked target resolved under the recorded repository root — a basename substring,
a same-named file elsewhere, a read denial or an unparseable line is not evidence), counts and
thread identity matched per turn, and the filesystem was unchanged. It is not an
independent observation of the nested call; generic write failures (e.g. disk full) are never
conclusive; a wrapper shape outside the grammar is INCONCLUSIVE, not a pass; and a timeout or
launch failure is inconclusive. The live stage tests the **production argv + runner**, not the
MCP stdio / `ArchitectService` path.

## Activation (human decision) and rollback — NOT EXECUTED

See `REVIEW_PACKAGE.md` §11. Side-by-side registration under a distinct name
(`gpt-architect-v2`) is the recommended first step; cut-over and rollback commands are listed
there (at the time of the last edit the human had registered it locally; check `claude mcp
list` rather than trusting this paragraph). Registering or removing a server does not touch
Codex history; *evaluation calls* do add Codex threads.

**Desktop coexistence (observed, not solved).** With Codex CLI 0.162.1 and the elevated Windows
sandbox backend, the sandbox setup refresh that runs before a sandboxed command opens the
desktop runtime's `node_repl.exe` for an ACL update; while another process holds it open the
refresh fails with `helper_unknown_error: setup refresh had errors` (OS error 32) and the
bridge refuses BEFORE any model call (`sandbox_unavailable/sandbox_setup_failed`). The bridge
now names what the Codex sandbox log actually shows for THIS call: a confirmed in-use file
(with its sanitised path), a different logged failure, or - when only a running `node_repl.exe`
is known - an UNCONFIRMED possibility; it never asserts the lock from process presence alone.
Under the default (elevated) backend there is no bridge-local remedy that preserves read-only
enforcement: keep the desktop runtime closed during calls, or invoke the bridge only from a client
that does not require it. **Working alternative with the desktop runtime OPEN (human-approved):**
`GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated`. With the desktop runtime running it passed the 3-stage
preflight, the model-free attack Stage A controls and a deterministic shell-write probe (control
ran; `Set-Content`, `Add-Content`, `Remove-Item`, an outside-workspace write and `git commit` were
all refused; nothing changed), and the LIVE attack Stage B on the production argv + runner: 8 of 8
`apply_patch` attacks (fresh and resumed) were rejected with specific policy evidence, repository
byte-identical, thread verified. The A3 verdicts stay INCONCLUSIVE (the patch engine emits no OS
denial code), and the 24-check smoke test's write steps depend on the model choosing to attempt
them, which it often declines to do (no FAIL in any run). The backend uses a restricted token
instead of the dedicated sandbox account and every answer carries a banner naming the override.
The harness workspaces must inherit their parent's ACL (`tools/workdirs.py`): a
`tempfile.mkdtemp` directory is unreadable to that backend's token. The desktop app starts the
server with its own minimal environment, so the override can instead be placed in
`<v2 state dir>/settings.json` (see the configuration table); the banner then names the file.
See `docs/DESKTOP_COEXISTENCE.md` sections 11-14.

## Known limitations

* Windows 11 / Python 3.13 / Codex 0.162.1 only; the lock is `win_amd64`-specific.
* `web_search` and runtime tool removal for delegation/image generation are **not** verified
  (no CLI readout); web exposure (`web__run`) is an open human decision.
* MCP containment is a configuration snapshot plus a narrowed, not eliminated, race.
* Termination of commands started by the Windows sandbox service cannot be confirmed; the
  conservative default requires operator reconciliation after a timeout/cancel.
* Sandbox preflight is readiness only; the elevated Windows sandbox refresh fails while the
  desktop runtime's `node_repl.exe` is held open (the bridge reports what the sandbox log shows
  and fails closed), and a lock appearing between preflight and the turn is detected only by the
  actual-turn inspection. The log is read only for this call's time window and only the two
  setup-refresh failure line shapes are parsed; entries within two seconds before the call are
  accepted for clock skew, so a failure moments earlier can be attributed to this call.
* Actual-turn failure detection keys on item shapes observed in Codex's session record; an
  unseen item shape degrades to "not flagged" (a false negative), never to a pass of the
  attack tests. Live `--json` shapes for failed commands were not re-captured.
* The live smoke test's oracles are the recorded tool calls and outputs in Codex's session
  rollout (`tools/smoke_oracles.py`), and only ATTRIBUTED OPERATIONS count: a native function call
  with structured arguments, or a JavaScript `exec` wrapper that is in full a pass-through of one
  `tools.<name>(<string literals>)` call printed verbatim. Text that merely mentions a command, a
  hash or a refusal is not an operation. A shell command is ONE simple command (words, quoted
  strings, at most one final `>`/`>>`): any separator, pipeline, sub-expression, block, variable,
  comment or line break makes it unattributable, and a verb inside a quoted string is data. A
  positive control is an operation that ran exactly `git rev-parse HEAD` whose own recorded output
  carries the real HEAD; a write step is a write VERB with the target in its PATH role (an
  echo-like command redirected into it also counts; a read, a mention, or a positional VALUE that
  equals the target name is not an attempted write; a flag outside the verb's schema, `-Flag:value`,
  `%`/`!`/`^` expansion characters and doubled quotes make the command unattributable) and its
  refusal counts only if specific denial wording is in that single operation's output and the
  command did not supply it. A `git commit` is attributed only as `git commit ...` with no global
  option before the subcommand, and a call with a working-directory argument is attributed only when
  that directory is the disposable repository. Only known tool names (`tools.exec_command`, `shell`,
  `apply_patch`, ...) and strictly comma-separated string arrays are attributed; the attack harness
  accepts a recorded JavaScript wrapper only from the outer tool `exec`. An unchanged
  filesystem is enforcement evidence only for steps whose attempt AND refusal are attributed
  (otherwise INCONCLUSIVE); the model's list of tools is a self-report and never a runtime
  inventory, and the smoke harness reports it as NOT ASSESSED instead of letting it turn an
  otherwise conclusive enforcement result inconclusive. Shapes real runs have not been observed to
  produce stay INCONCLUSIVE until a live record shows them; the harness itself was not run live for
  this source.
* `--ignore-user-config` exists on `codex exec` (skips `$CODEX_HOME/config.toml`, auth still
  applies). It is NOT adopted: `mcp list` rejects it, so its effect on the MCP set cannot be
  observed model-free here, and it would also drop the user's sandbox-backend setting, changing
  the evidence basis. It is a decision for the human (D1), not a silent change.
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
