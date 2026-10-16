# GPT Architect ⇄ Claude Code MCP bridge — v2 (review candidate, post-remediation)

**Status: isolated candidate. NOT registered, NOT activated.** v1 (`C:\AI\gpt-claude-mcp-bridge`)
is untouched and remains the live bridge. Decisions D1, D3 and D8 are pending (human).
Evidence for this candidate is in `REVIEW_PACKAGE.md` and `docs/evidence/` and is bound to a
source hash; results recorded for earlier candidates are *not* evidence for this code.

Same contract as v1: a local stdio MCP server that lets Claude Code consult a GPT architect
through the **ChatGPT-authenticated Codex CLI** (subscription allowance only; no API client,
API key or API fallback). Exactly two tools: `consult_architect(question: str)` and
`review_implementation(focus: str)`; text responses.

```
architect_bridge.py        MCP entry point (same invocation as v1)
bridge_admin.py            local administration CLI (not MCP tools)
bridge/                    implementation package
tests/  tools/  docs/      tests + fake CLI, helper/evidence scripts, notes and evidence
requirements.lock.txt      hash-pinned Python dependency set (win_amd64, py3.13)
```

## Threat boundary and the precise read-only claim

**Claim (bounded).** With the Codex CLI at a supported version, the sandbox healthy, and the
documented capability policy applied, the bridge pins `sandbox_mode="read-only"` and
`approval_policy="never"` on every fresh and resumed call, and the *native repository write
routes that were tested* — shell commands and Codex's `apply_patch` — were rejected in the
recorded test runs (see `REVIEW_PACKAGE.md`, evidence classes and hashes).

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
  unavailable (the bridge then refuses; it never degrades silently).

## What is enforced, requested, or unverified

Every capability control is reported at one of three evidence levels (`bridge_admin.py
status`, telemetry `capabilities`):

| Level | Meaning | Examples |
|---|---|---|
| **resolved** | the CLI's *own effective-config readout* shows the control applied (`codex features list` / `codex mcp list --json` with the overrides) | feature gates: hooks, plugins, apps, browser/computer use, **multi_agent** (delegation), **image_generation**; MCP servers |
| **requested** | passed to the CLI; the CLI offers no readout to confirm it | `web_search="disabled"` (a key in the CLI's config schema; the observed `web__run` tool is not mapped to it); the read-only sandbox / approval flags (argv-pinned and guard-checked) |
| **uncontained** | no control exists or none is used | `view_image` (local image read), goals/sleep, skills under `~/.codex` and `~/.agents` |

"resolved" means the CLI says the gate is off, **not** that tool removal was observed at
runtime; the model-visible prompt text still mentions delegation with the gate off, so prompt
text is not evidence either way. Runtime tool availability is only observable with a live
tool listing (proposed, not executed in this pass).

* The MCP allow-list (`GPT_ARCHITECT_MCP_ALLOW`) is **empty by default**. An allowed server
  is *not contained*; it is named in every returned answer (`[bridge-warning]`), in
  telemetry (`capabilities.mcp_allowed`, warnings) and in `status`, without paths or secrets.
* **Fail closed:** a missing required control (`required_control_missing`), an unparseable
  `features list` / `mcp list` schema (`*_schema_incompatible`), a gate still on after the
  override (`feature_disable_not_effective`), or an MCP server still enabled
  (`mcp_still_enabled`) aborts before inference.
* **Web:** disabled by request; there is deliberately **no exception mechanism**. Any web
  exception needs a written egress/research policy (what may be queried, that repository
  content is never put in external queries by default) approved by the human — pending (D8).
* **Delegation / image generation:** controlled by their own gates and reported separately.
  If a future CLI lacks a gate the call fails closed rather than claiming containment.

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
| `GPT_ARCHITECT_ON_TURN_INFRA_FAILURE` | `error` | `error` withholds an answer whose actual turn hit infrastructure failures; `degraded` returns it with a prominent banner |
| `GPT_ARCHITECT_SANDBOX_PREFLIGHT` | `1` | `0` skips the readiness preflight (not recommended; recorded) |
| `GPT_ARCHITECT_WINDOWS_SANDBOX` | unset | `elevated`/`unelevated`; unset = your Codex config decides. Changes the sandbox implementation; an override is a configuration without attack-test evidence and is bannered in every answer and recorded in telemetry |
| `GPT_ARCHITECT_ALLOW_UNTESTED_CLI` | `0` | `1` = run with an unsupported CLI version (recorded, bannered) |
| `GPT_ARCHITECT_V2_STATE_DIR` | `~/.gpt-architect-bridge-v2` | state (outside the repo, separate from v1) |
| `GPT_ARCHITECT_CODEX_PATH` | PATH lookup | explicit launcher (advanced / tests) |
| `CLAUDE_PROJECT_DIR` | cwd | repository root (must contain `.git`) |

## Authentication

`codex login status` is classified every call (ChatGPT required; missing / API-key /
unsupported refused with actionable text). The bridge never logs in or out, never reads
`auth.json`, and removes `CODEX_API_KEY` / `OPENAI_API_KEY` from every child process.

## Sandbox preflight is readiness, not proof — and the actual turn is inspected

*Before* inference the bridge runs three model-free `codex sandbox` stages with the call's
own overrides and cwd: (1) a process can start; (2) `git rev-parse --is-inside-work-tree`;
(3) a small tracked **source file** can be read. Failure is `sandbox_unavailable`
(`sandbox_setup_failed`, `sandbox_timeout`, `repo_unreadable`, `source_file_unreadable`)
with no allowance used. Limits, stated plainly:

* a passing `git rev-parse` does **not** prove source files are readable (stage 3 samples
  one file and still proves nothing about the others);
* `codex sandbox` is not the same code path as `codex exec`; preflight and the real turn can
  differ, and state can change between them (the ChatGPT app can start `node_repl.exe` in
  that window).

Therefore the **actual turn** is inspected: completed command/tool items whose own output
reports sandbox setup, process-creation or repository-access failures
(`helper_unknown_error`, `Failed to create unified exec process`, `fatal: not a git
repository` in the repository cwd, …) make the call fail with
`sandbox_unavailable/turn_*` and the answer is **withheld** (default), even if the turn
"completed" with a plausible answer. Only items that *report failure* are considered, and
agent narration is never scanned, so an ordinary non-zero exit (`git diff` → 1) or a file
that merely contains these words is not an infrastructure failure. The bridge never infers
successful repository inspection from model narration.

## Process ownership and termination (F3)

The Codex child is placed in a **Windows Job Object** (`KILL_ON_JOB_CLOSE`, no breakaway):

* it owns every descendant (created after assignment), so timeout/cancel use
  `TerminateJobObject`, and a parent exiting with live descendants is detected and the
  descendants are reaped — a session is not considered free merely because the CLI exited;
* the job's live-process count makes termination **confirmed** (0 members) rather than
  attempted; messages say `CONFIRMED` or `ATTEMPTED but NOT CONFIRMED`;
* if the **bridge itself is hard-killed**, the OS closes the job handle and kills the
  members; the next call sees the stale in-flight marker, records an automatic
  reconciliation and warns;
* if termination cannot be confirmed (or ownership could not be established) an
  **uncertain in-flight marker** blocks the session (`session_in_doubt/uncertain_termination`)
  until `bridge_admin.py reconcile --yes`. Recovery never kills anything.

**Limits:** assignment happens right after `Popen` (a process spawned by the child in that
millisecond window is not a member); commands launched by the Windows sandbox **service**
(`codex-windows-sandbox-service.exe`) are outside the job and can neither be enumerated nor
confirmed terminated by this mechanism; nested-job assignment can fail on old Windows
(ownership is then reported as unavailable and termination is never called confirmed).

## Sessions, registry lifecycle and recovery (F4/F5)

State directory: `instance.json`, `registry.json` (+ `registry.prev.json`), `locks/`,
`inflight/`, `pending/`, `journal.jsonl` (content-free), `maintenance.json`, `telemetry/`.

* **First initialisation ≠ loss.** `instance.json` records `registry_established`. A missing
  registry on an established instance is `session_corrupt/registry_lost`: the bridge refuses
  and never starts replacement sessions; a corrupt or foreign (`instance_id` mismatch)
  registry is left untouched.
* **Quiescence.** Registry-wide commands (`quarantine`, `reinit`, `restore-prev`) set a
  maintenance flag (new calls refuse), require that no session lock is held, and cannot be
  undermined by an in-flight call: a late `bind_thread` against a lost registry fails
  (`registry_lost`) instead of repopulating it.
* **Reset retires bridge pointers only.** Codex history is never deleted or modified.

```powershell
bridge_admin.py status [--repo P] [--all] [--json] [--sandbox]
bridge_admin.py new|reset|adopt|reconcile ...   # per session; reset/adopt/reconcile need --yes
bridge_admin.py quarantine|reinit|restore-prev|end-maintenance --yes   # registry-wide
```

### Partial and uncertain outcomes (F5)

The bridge *pointer* and Codex *history* are different things; the bridge never claims history
is unchanged merely because the registry was not updated.

| Outcome | Returned | Pointer | Next call |
|---|---|---|---|
| `success` | answer | bound | normal |
| `degraded` (opt-in) | answer + DEGRADED banner | bound | normal |
| `success_partial_persistence` | answer + PARTIAL SUCCESS banner with thread id | **not saved**; pending marker written | refused (`pending_persistence`) until `adopt` or `reset` |
| `success_session_in_doubt` | answer + in-doubt banner | bound | refused (`uncertain_termination`) until `reconcile` |
| timeout / cancelled / error after inference started | error naming the thread id (if observed) | unchanged | normal (a thread observed on a fresh turn is journalled as `unbound_thread`) |

Errors after inference started always say *"Codex history MAY contain a partial or
complete turn in thread …"*; only a call that never started a turn says history is
unaffected. Nothing is ever replayed.

## Locks: what they coordinate (and what they do not)

Session and registry locks are advisory OS file locks that coordinate **cooperating
processes sharing this v2 state directory**. They do not cover v1 (separate state), a manual
`codex resume` of the same thread, the Codex desktop app, or any other writer. Lock order is
fixed: session lock → registry lock (brief) → journal/telemetry lock (leaf); admin commands
use maintenance flag → quiescence probes → registry lock and never take a session lock while
holding the registry lock. **Session locks do not freeze the repository** (next section).

## Review consistency (F6)

Before and after every turn the bridge fingerprints the repository: `HEAD`, `git status
--porcelain=v1 -z`, a streamed hash of `git diff --binary HEAD`, and size+mtime of untracked
paths — with `--no-optional-locks`, so the bridge never writes `.git/index`. A change during
the review is reported in the answer (`[bridge-warning]`), in telemetry (`repo.changed`,
digest prefixes only) and in warnings; no paths, diffs or source enter telemetry. If the
fingerprint is unavailable that is reported too.

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

## Telemetry

`<state dir>\telemetry\calls.jsonl` (rotating, cross-process safe), one terminal record per
invocation, redacted by construction: ids, route, session/thread, durations, outcome, error
category/code, usage (`null` when not reported), event/infra counters, capability evidence
summary, process ownership/termination, repository digests (prefixes), pointer/history/session
state, prompt/answer lengths. Never prompt text, source, answers, credentials, error prose or
repository paths. Examples: `docs/telemetry_examples_remediation.jsonl` (fake CLI; fixture
values, including mocks that force uncertain termination).

## Tests and checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v      # no subscription use
.\.venv\Scripts\python.exe tools\record_evidence.py tests --label <name>  # bind result to source hash
.\.venv\Scripts\python.exe tools\v1_manifest.py compare docs\v1_baseline_before.json
```

Proposed **opt-in live** checks (consume allowance; NOT executed in the remediation pass):
`tools\live_smoke_test.py` and `tools\apply_patch_attack_test.py --live` (see `REVIEW_PACKAGE.md`
§7). The attack test judges each expected attack individually from Codex's own session record
with specific rejection evidence, exact per-turn counts, thread identity and the filesystem;
its live stage tests the **production argv + runner**, not the MCP stdio / `ArchitectService`
path.

## Activation (human decision) and rollback — NOT EXECUTED

See `REVIEW_PACKAGE.md` §7. v2 is not registered. Side-by-side registration under a distinct
name (`gpt-architect-v2`) is the recommended first step; cut-over and rollback commands are
listed there. Keep the ChatGPT/Codex desktop apps closed while the bridge runs (the Windows
sandbox refresh fails while their `node_repl.exe` is alive).

## Known limitations

* Windows 11 / Python 3.13 / Codex 0.162.1 only; the lock is `win_amd64`-specific.
* `web_search` and runtime tool removal for delegation/image generation are **not** verified
  (no CLI readout); web exposure (`web__run`) is an open human decision.
* Termination of commands started by the Windows sandbox service cannot be confirmed.
* Sandbox preflight is readiness only; the elevated Windows sandbox refresh fails while a
  `node_repl.exe` runs, and a lock appearing between preflight and the turn is detected only
  by the actual-turn inspection.
* Actual-turn failure detection keys on item shapes observed in Codex's session record; an
  unseen item shape degrades to "not flagged" (a false negative), never to a pass of the
  attack tests.
* A failed first turn leaves a thread in Codex history; it is journalled, not deleted.
* Rate-limit/auth/session-missing detection is message-pattern based.
* Hook discovery is a heuristic over `CODEX_HOME` and `<repo>/.codex`; hooks are controlled
  by the `hooks` gate (resolved), not by discovery.
* Single-user design; named sessions are selected per MCP server process, not per call.
