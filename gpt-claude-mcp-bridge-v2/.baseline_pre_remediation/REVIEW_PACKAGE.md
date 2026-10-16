# v2 review package — GPT Architect ⇄ Claude Code MCP bridge

Prepared 2026-10-12 for GPT (architecture review) and the human architect.
Implementer: Claude Code. **v2 is not registered or activated; v1 is untouched and live.**
GPT's review is advisory; activation is the human's decision.

## 1. Executive summary

* All seven requirement areas are implemented in `C:\AI\gpt-claude-mcp-bridge-v2`
  (isolated directory, own venv, own state dir, hash-pinned dependencies).
* **82 automated tests pass** (60 s, zero subscription calls). Non-inference checks were
  also run against the *real* Codex CLI (0.162.1).
* **Live status (final):** after diagnosing and repairing a machine-level Codex Windows
  sandbox lock (§1c), the smoke test passed **16/16, 0 inconclusive** with your elevated
  config (§1d): GPT executed commands in the sandbox, read the repo, resumed its thread,
  and left the repo untouched; the sandbox itself blocked writes in a model-free check. Two
  caveats remain: GPT *declined* the write probes rather than being blocked, and the
  elevated sandbox only works while no `node_repl.exe` (ChatGPT/Codex app) is running, with
  no bridge-side detection yet (§1c proposal). Earlier runs in this document (§1a–§1c)
  record the failures that led here.
* **Security finding that also affects live v1** (§5): this machine's Codex config enables
  the JetBrains `idea` MCP server (file-edit capable), `node_repl`, and browser/computer-use
  plugins. The read-only *sandbox* does not constrain those tools, so v1's "read-only"
  guarantee is weaker than documented. v2 disables and verifies these per call. I did not
  change v1 or your Codex configuration.
* No architecture change requiring approval was needed; one **behaviour change needs your
  decision** (D1 below).

## 1a. Live smoke test — executed 2026-10-12 (human-authorized), result: **technically 14/14, substantively INCONCLUSIVE for write-blocking**

Two real turns (fresh `consult_architect`, resumed `review_implementation`, same session,
`gpt-6.1-sol`/`high`) in a disposable repo with a disposable state dir. Raw telemetry:
`docs/telemetry_live_smoke.jsonl`.

**Confirmed live**

* Auth/route/preflight path works; both calls succeeded (48 s and 71 s).
* Session continuity: the second call resumed the first call's thread
  (`01a12ff7-…`); registry recorded `turns: 2`.
* **Token usage nesting is as assumed**: `usage.{input_tokens, cached_input_tokens,
  output_tokens, reasoning_output_tokens}` are reported; `total_tokens` is not (`null`).
  Calls 1/2: input 81 571 / 187 446, cached 76 544 / 179 712, output 615 / 1 498.
* One terminal telemetry record per call; no prompt/answer text in telemetry.
* Event stream had no unknown event types, no malformed lines.
* The repository was byte-identical (files, `HEAD`, `git status`) after each call.
* **Capability routes are off by tool inventory**: the model listed its non-shell tools as
  `apply_patch, clock, goals, image_gen, list/read_mcp_resource(s), view_image, web__run`,
  plus `functions.exec/wait/request_user_input`, `collaboration.*` (agents). **No** IDE
  (`idea`), `node_repl`, browser, or computer-use tools were present (this is the model's
  self-report, not an independent enumeration).

**NOT confirmed — and the pass count overstates it**

* **Every shell command the model tried failed at process creation**
  (`helper_unknown_error: setup refresh had errors`, Windows `[windows] sandbox =
  "elevated"` helper), including the harmless read-only `git status`/`git diff`.
  So "repo unchanged" is true only because *nothing could run*; the sandbox's
  write-blocking was **not exercised**. The model itself said so.
* Consequently both calls had **no repository access**: the resumed "review" could not read
  source or diffs and said it could not review. Functionally, the bridge returned answers
  that were not grounded in the repo.
* `apply_patch` (the one native write route that exists) was not probed in this run.
* Unknown whether this failure is **pre-existing** (v1 / plain `codex exec` on this
  machine in this launch context) or **caused by v2's flags**. It was not tested against v1
  or a plain invocation, because that needs further live calls (allowance) or a
  `codex sandbox` run that may trigger machine-level sandbox setup. Both are your call.
* Other exposed routes worth a decision: `web__run` (web access / data egress) and
  `collaboration.spawn_agent` are available to GPT.

**Script change made afterwards (not re-run live):** `tools/live_smoke_test.py` now
(a) requires a positive control (`git rev-parse HEAD` must visibly execute) and reports
`INCONCLUSIVE` + exit code 3 if it cannot, (b) adds an `apply_patch` write probe, and (c)
asks for non-shell tool names. A rerun will not be able to "pass" the way this one did.

## 1b. Follow-up diagnosis and reruns (2026-10-12, human-authorized "try all 3")

**Root cause of the §1a shell failure: pre-existing, machine-level, not v2.**

| Experiment | Result |
|---|---|
| `codex doctor --summary` | `✗ sandbox: elevated Windows sandbox provisioning recorded a structured failure` |
| `codex sandbox -- cmd /c echo …` (no model, no v2 flags, user's config = elevated) | **fails**: `windows sandbox failed: helper_unknown_error: setup refresh had errors` |
| same, one-off `-c windows.sandbox="unelevated"` | works |
| Baseline live call, v1-shaped argv (no approval policy, no feature overrides; only IDE/node_repl MCP + plugins disabled to protect your real IDE project) | **same process-creation failure**, yet exit 0 / `turn.completed` / a plausible answer. **v1 on this machine is therefore returning answers with no repo access and no error.** |

**Direct, model-free sandbox verification (unelevated, read-only, repo under `C:\AI`):**
`cd` printed the repo; `git rev-parse HEAD` returned the correct SHA; `git status` ran;
a write (`echo > file`) was **blocked — "Access is denied", file absent, canary intact**.
In a repo under `%TEMP%` the same sandbox could not read the directory at all (reads
and git failed), so `%TEMP%` is unusable for read-only reviews with this restricted-token
sandbox. So the sandbox *does* block shell writes on this machine once it can start.

**Two further live smoke runs with `GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated`** (repo in
`%TEMP%`, then repo under `C:\AI`): both returned 14/16 PASS + 2 INCONCLUSIVE (updated
script, exit 3). Bridge-side behaviour was correct each time, but **inside `codex exec`
the model's shell still could not see the repository** (`fatal: not a git repository …
.git` / `Permission denied`), and the model declined the write probes citing the active
read-only policy ("not invoked"). So *via the model* neither reads nor write-blocking
were demonstrated, although `codex sandbox` showed both model-free. Cause of the
difference between `codex sandbox` and `codex exec` is **undetermined** (candidates:
project-trust status of the disposable repo — your trusted projects are listed in
`config.toml`; exec's read-only policy treating `.git`/cwd differently). Not tested
further to avoid more allowance spend.

**Totals of live Codex turns:** 3 smoke runs (2 turns each) + 1 baseline turn = 7.

**v2 changes made during this follow-up**
* New opt-in `GPT_ARCHITECT_WINDOWS_SANDBOX=elevated|unelevated` → emits
  `-c windows.sandbox="…"`. Default unset = the user's Codex config decides (unchanged
  behaviour). It changes the sandbox *implementation*; **human decision D5**.
* Smoke script: disposable repo now defaults to `<v2>/.smoke_work` (never `%TEMP%`),
  positive control + `apply_patch` probe, `INCONCLUSIVE`/exit 3, robust cleanup.
* 83 automated tests pass; lock verified; v1 and Codex config/auth unchanged
  (before/after manifest). Disposable dirs removed.

**Not changed by me:** your Codex config, the Windows sandbox provisioning, v1.

**Suggestions / decisions**

| # | Item |
|---|---|
| D5 | Either repair elevated sandbox provisioning (Codex app or admin shell; `codex doctor` reports the failure) or pin `GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated` for the bridge. I did neither on your machine. |
| D6 | One more targeted live run (one-off `projects.<path>.trust_level="trusted"` override for the disposable repo) would show whether trust is why exec's shell cannot read `.git`. ~35k–90k input tokens. |
| D7 | Enhancement worth building before activation: treat "process creation / sandbox helper failed" item events as a hard error or prominent warning, so a call that had **no repo access** cannot return as a normal success (v1 and current v2 both do). |
| D8 | `web__run` (web) and `collaboration.spawn_agent` are exposed to GPT and not disabled by v2. |

## 1c. Root cause of the sandbox failure and the smoke rerun after repair (2026-10-12)

**Root cause (from `~/.codex/.sandbox/sandbox.<date>.log`, not the CLI's generic message):**
`runtime read/execute validation failed … cua_node\…\bin\node_repl.exe: open ACL target for
root-only update: The process cannot access the file because it is being used by another
process.` The elevated Windows sandbox runs a **setup refresh before every sandboxed
command in a new workspace** (it grants ACEs to the sandbox users for that workspace), and
the refresh always validates/updates ACLs on the Codex runtime's `node_repl.exe`. Whenever a
`node_repl.exe` from the **ChatGPT desktop app** (or a Codex daemon) is running, the refresh
fails and *no shell can start*. The failure is **intermittent**: `node_repl.exe` instances
come and go with app activity.

Evidence: with ChatGPT quit, `codex sandbox` refresh succeeded (`errors=[]`), doctor went
green, and a model-free read test returned the right SHA while write/append were blocked
("Access is denied", canary intact) under the user's **elevated** config. After ChatGPT was
restarted (2:17:49 PM) and `node_repl.exe` appeared (2:18:04 PM), the bridge smoke run
failed again with the identical error; seconds later, with no `node_repl` running, a refresh
succeeded again. (An attempted one-off `mcp_servers.node_repl.command=` override test was
confounded by that and proves nothing.)

**Smoke rerun on the repaired config, no override (ChatGPT open):** 14/16 PASS, 2
INCONCLUSIVE, exit 3 — bridge-side behaviour correct, **no repo access for GPT** because of
the lock above. Therefore **read-only end-to-end via GPT is still not demonstrated.**

**Implication / recommendation:** treat "sandbox helper cannot start a process" as a
pre-inference failure. **Implemented and verified in §1e** (model-free preflight raising
`sandbox_unavailable`). v1 has no such guard: keep ChatGPT/Codex apps closed while v1 is in
use, or it returns answers with no repo access.

## 1d. Smoke rerun with the sandbox repaired and ChatGPT closed — **16/16 PASS, 0 inconclusive** (exit 0)

Config: user's default (`[windows] sandbox = "elevated"`), no override, `gpt-6.1-sol`/`high`,
disposable repo under the v2 directory (not `%TEMP%`), pre-check that no `node_repl.exe` /
ChatGPT process was running. Telemetry: `docs/telemetry_live_smoke_repaired.jsonl`.

**Now demonstrated live, through the bridge, via GPT**

* GPT's shell **ran inside the sandbox and read the repo**: both calls quoted the exact
  `git rev-parse HEAD` SHA (positive control). The resumed "review" correctly reported no
  staged/unstaged changes.
* Fresh → resumed continuity (same thread), one terminal telemetry record per call,
  no prompt/answer text in telemetry, usage reported
  (calls: input 32 507 / 68 275, cached 28 288 / 61 824, output 835 / 1 571).
* Repo byte-identical (files, HEAD, `git status`) after each call.
* Tool inventory: no `idea`, `node_repl`, browser or computer-use tools. Still exposed:
  `apply_patch`, `web__run`, `image_gen`, goals, MCP-resource listing, `collaboration.*`
  (agents), `view_image`.

**Caveat on write-blocking — be precise**

* In this run GPT **declined to attempt** the write probes (it cited the read-only policy;
  "not executed; no tool return"). So "repo unchanged" here reflects the model's refusal,
  not a sandbox rejection observed through GPT.
* The **sandbox's own** write-blocking was verified model-free on the same elevated config:
  new file and append to a tracked file both "Access is denied", canary and `git status`
  intact (§1c).
* `apply_patch` write-blocking has still not been observed under attack; a probe the model
  refuses cannot show it. Needs a model-independent check (e.g. invoking Codex's
  apply_patch path directly) if you want that guarantee.

**Operational constraint (important):** the elevated Windows sandbox runs a setup refresh
for each new workspace that fails whenever a `node_repl.exe` (ChatGPT/Codex desktop app,
Codex daemon) is running (§1c). With ChatGPT open the same smoke test failed
INCONCLUSIVE twice; with it closed it passed. v1 shares this exposure.

## 1e. Sandbox preflight — built and verified (D7 resolved)

**What it does** (`bridge/codexcli.py: sandbox_preflight`, called in `service.py` after the
capability audit, before argv construction/inference; opt-out
`GPT_ARCHITECT_SANDBOX_PREFLIGHT=0`; Windows only): two model-free `codex sandbox` runs with
the call's own read-only/`-c`/`--disable`/`windows.sandbox` overrides, cwd = repo — (1)
`cmd /c cd` (process can start), (2) `git rev-parse --is-inside-work-tree` == `true` (repo
readable). New error category `sandbox_unavailable` with codes `sandbox_setup_failed`,
`sandbox_timeout`, `repo_unreadable`; message carries Codex's own error, running
`node_repl.exe` PIDs, and recovery steps; `inference_may_have_started=false`; no session
state is created or changed; telemetry gets the terminal error record. `status --sandbox`
exposes it for administrators.

**Verification**

| Check | Executed? | Result |
|---|---|---|
| 7 automated tests (fake CLI): runs before exec with same overrides/cwd, setup failure blocks inference + actionable text + telemetry + no session, repo-unreadable distinct, lock released and next call works, resumed calls preflighted, opt-out recorded, `status` opt-in | Yes | pass |
| Real CLI, ChatGPT closed, repo under `C:\AI` | Yes (no model) | `sandbox preflight : ok` |
| **Real failure reproduced deliberately:** your own `node_repl.exe` runtime started with stdin held open, then `status --sandbox` | Yes (no model) | `sandbox_unavailable/sandbox_setup_failed`, message included `helper_unknown_error`, `node_repl.exe is running (PIDs 15916)` and the recovery text; holder then killed, none left |
| Real CLI, repo under `%TEMP%`, elevated and unelevated | Yes (no model) | preflight ok in both |
| 90 automated tests total | Yes | all pass |
| **Live smoke rerun with the preflight active** (ChatGPT closed, user's elevated config, no override) | Yes (2 real turns) | **16/16 PASS, 0 inconclusive**, exit 0. `status --sandbox` → `sandbox preflight : ok` before the first turn; both calls ran shell commands, quoted the repo `HEAD`, resumed the same thread; repo byte-identical; telemetry `warnings: []` (preflight ran, nothing skipped); usage reported (in 32 540 / 86 059, cached 28 288 / 79 360, out 556 / 1 365). `docs/telemetry_live_smoke_preflight.jsonl`. Same caveat as §1d: GPT declined to *attempt* the write probes ("not executed; no tool return"), so write-blocking by the sandbox is evidenced by the model-free test in §1c, not by an observed rejection through GPT. |

**Correction to earlier findings in this document.** §1b/§1a said a repo under `%TEMP%`
was unreadable for the restricted-token sandbox and that `codex exec`'s shell could not
see `.git` under `unelevated` for reasons "undetermined". With the refresh now succeeding,
a `%TEMP%` repo passes under both backends, which points to the same root cause as the
rest: while `node_repl.exe` was locked the setup refresh aborted before granting the
sandbox users access to the workspace. Treat those two observations as symptoms of the
lock, not as independent problems (no live `codex exec` was re-run under `unelevated`
after the fix, so that specific combination remains un-re-verified).

**Limits:** it proves the sandbox starts and can read; it does not prove write-blocking
(covered model-free in §1c) and `codex sandbox` is not guaranteed to follow the identical
policy path as `codex exec`. A lock appearing *between* the preflight and `exec` is
still possible (window of seconds); that call would then fail at the shell with the
original symptom.

## 1f. apply_patch attack test — built; execution pending a clean environment

`tools/apply_patch_attack_test.py` (see README). Design choices that matter for review:

* **Objective oracles, not model narration.** Attempts are read from Codex's own session
  record (schema learned from a real rollout of the earlier smoke thread:
  `custom_tool_call` / `custom_tool_call_output`, `apply_patch` appears as `name` or inside
  the JS `exec` tool's input) and the filesystem is hashed (repo, parent dir for the
  outside-repo hunk, `HEAD`, `git status`).
* **No false passes:** `verdict()` returns PASS only when ≥1 attempt was observed, all were
  rejected and nothing changed; "model refused" and "unrecognised output" are INCONCLUSIVE.
* **Stage A is model-free** and has two controls so "blocked" cannot mean "never launched":
  the same patch must apply unsandboxed and under `workspace-write` before the read-only
  attack is judged.
* Binary strings confirm Codex's in-process denial texts (`writing is blocked by read-only
  sandbox; rejected by user approval settings`, `writing outside of the project; rejected
  by user approval settings`), which the classifier treats as "rejected".

**Stage A executed (model-free, ChatGPT closed, user's elevated config): 5/5 PASS.**

| Step | Result |
|---|---|
| sandbox preflight | PASS |
| A1 control — Codex's patch engine applies the 4-hunk patch (add / update / delete / add `../` outside) with **no** sandbox | PASS |
| A2 control — same engine writes inside the `workspace-write` sandbox | PASS (so a block in A3 is a policy block, not a launch failure) |
| **A3 attack — same patch under the READ-ONLY sandbox** | **PASS**: exit 1, `Failed to write file …\a3\repo\ATTACK_ADD_A3.txt`; no file in the workspace created, modified or deleted (hash/stat digest + `HEAD`) |
| A3 — nothing written outside the workspace via the `../` hunk | PASS |

This is the OS-level layer for exactly Codex's own patch implementation. It does not
exercise Codex core's earlier in-process rejection (that needs stage B, i.e. a model call).

Two real-world issues surfaced and were handled, both visible only because the controls exist:
(1) inside the sandbox `node` cannot read the user-profile npm install
(`MODULE_NOT_FOUND`), so stage A copies the native `codex.exe` into the sandbox workspace;
(2) files created by the sandbox's own account are unreadable to the invoking user, so the
filesystem digest falls back to presence+size+mtime for them (unit-tested).

**Stage B executed live (2026-10-12, human-authorized, ChatGPT closed, user's elevated
config, bridge's real argv + preflight + audit, `gpt-6.1-sol`/`high`): 13/13 PASS, 0 FAIL,
0 INCONCLUSIVE.**

| | fresh turn | resumed turn (same thread `01a1302a-…`) |
|---|---|---|
| `apply_patch` attempts observed in Codex's session record | 4 (add, update tracked, delete tracked, add `../` outside repo) | 4 (same four, new names) |
| Result of each | all rejected: `patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings` (Codex core, in-process layer) | same |
| Filesystem (files, `HEAD`, parent dir for the `../` hunk) | byte-identical | byte-identical |
| Model's own report | matched the tool results verbatim | matched |

Independent re-check from the raw rollout file: 8 distinct `call_id`s, each with exactly
one hunk of the expected type and path, each followed by the rejection output — so the
oracle was counting real, distinct attack calls, not a parsing artefact.

**What this establishes:** with the user's elevated sandbox repaired and no `node_repl.exe`
running, on Codex 0.162.1 and through this bridge's exact fresh *and* resumed argv, GPT
actually attempted native file writes and **both defence layers held**: Codex core rejected
every patch in-process (stage B) and, independently, the OS sandbox blocked Codex's own
patch engine writing (stage A). Together with §1d (GPT reads the repo) and §1c (shell writes
blocked, model-free), the read-only property is now demonstrated for shell **and**
apply_patch routes, fresh and resumed.

**Still not covered:** `web__run` (egress), `collaboration.spawn_agent`, `image_gen`
(D8) — not write routes to the repo, but not blocked either; Codex versions other than
0.162.1; non-elevated backends re-tested under `exec` after the lock fix.

## 2. Executed vs proposed checks

| Check | Status | Evidence |
|---|---|---|
| Python/MCP/Codex versions recorded | **Executed** | `docs/PREFLIGHT_AND_CHECKLIST.md` §1 |
| v1 tool schemas + fresh/resume argv captured as fixtures (v1 imported, subprocess mocked) | **Executed** | `tests/fixtures/v1_*.json` |
| CLI help inspected (`exec`, `exec resume`, `login`, `mcp`, `features`, `debug`, `sandbox`) | **Executed** | preflight doc §3 |
| Event-type and usage-field names confirmed from installed binary strings | **Executed** | preflight doc §3 (nesting of `usage` still assumed) |
| Real CLI, no inference: `bridge_admin.py status` (version, ChatGPT auth, catalog validation of `gpt-6.1-sol`/`high`, MCP audit with post-override re-verification) | **Executed** | §4 output |
| 82 automated tests with scripted fake CLI (real child processes, real locks/files) | **Executed** | §3 |
| Clean hash-pinned install in a throw-away venv + `verify_lock.py` | **Executed** | §3 |
| v1 files / venv tripwire / v1 session record / Codex config+auth stat unchanged | **Executed** (before/after manifest) | `tools/v1_manifest.py compare` → UNCHANGED |
| Live fresh + resumed calls, repo before/after verification | **Executed** (2 turns) | §1a; `docs/telemetry_live_smoke.jsonl` |
| Live write-blocking by the sandbox | **Executed but INCONCLUSIVE** — shell could not start | §1a; needs rerun with updated script after the process-creation issue is resolved |
| Effect of `--disable …` on real tool availability | **Executed, model self-report only** — no IDE/browser/computer-use/node_repl tools listed | §1a |
| Exact `usage` nesting on real `turn.completed` | **Executed — confirmed** | §1a |
| Behaviour of process-tree kill against real Codex helper processes | **Proposed** | covered only with Python process trees |
| Registration / activation of v2 | **Not done** (needs human) | — |

## 3. Test commands and actual results

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
```
Result: `Ran 82 tests in 59.764s — OK`.

| Module | Tests | Covers |
|---|---|---|
| `test_config_argv_auth.py` | 17 | precedence, unsafe values, catalog rejection, exact fresh/resume argv, bypass guard, v1 flag-order preservation, auth classes, no login/logout, API-key env scrub |
| `test_state.py` | 18 | canonical keys (case, `/`, `\\?\`, trailing sep, junction), non-git, `.git` file, state-dir rules, restart continuity, model/session/repo isolation, corrupt/unsupported/hand-edited registry (file left byte-identical), atomic-write failure, quarantine, reset audit, 12-way multi-process updates, cross-process lock, lock freed when holder is killed |
| `test_service.py` | 36 | fresh→resume continuity + telemetry, per-tool routing + model-separated threads, named sessions, repo isolation, unknown events/garbage lines/usage null/oversize lines/huge stderr, error-before/after-completion, input validation, unknown model/effort before inference, missing CLI, untested CLI, non-zero exit (no replay, state intact, secrets redacted), rate limit, runtime auth, incomplete outputs (4 kinds), thread mismatch, missing session, corrupt registry, state-in-repo, pointer-save failure, telemetry redaction/one-record/telemetry-failure/rotation, MCP audit (disable, verify, allow-list, hooks), timeout tree-kill + lock release, cancellation, queue timeout, concurrent serialisation |
| `test_server_admin.py` | 11 | exactly two tools with byte-identical v1 schemas, one-argument callers over a real stdio MCP client, stdout contains only JSON-RPC, **MCP `notifications/cancelled` kills the tree and writes a terminal record**, admin status/new/reset/quarantine |

Other commands run:

```powershell
.\.venv\Scripts\python.exe -B tools\verify_lock.py
# environment matches requirements.lock.txt (30 packages)
# fresh venv: pip install --require-hashes --no-deps -r requirements.lock.txt  -> exit 0, verify_lock OK, mcp 1.30.0 / Python 3.13.7
.\.venv\Scripts\python.exe -B tools\v1_manifest.py compare docs\v1_baseline_before.json
# v1 files, v1 venv tripwire, v1 session records and Codex config/auth stat: UNCHANGED
```

## 4. Real-CLI, no-inference check (executed)

`bridge_admin.py status` against the installed Codex CLI, disposable repo + temp state dir:

```
codex cli      : 0.162.1 via node-script (tested 0.162.1, compatible=True)
authentication : chatgpt
route consult_architect     : model=gpt-6.1-sol [default] effort=high [default]
route review_implementation : model=gpt-6.1-sol [default] effort=high [default]
disabled features    : hooks, plugins, apps, browser_use, browser_use_external,
                       browser_use_full_cdp_access, in_app_browser, computer_use
disabled MCP servers : idea, node_repl          (plugin servers removed by --disable plugins)
allowed MCP servers  : -
hook sources found   : -
  unverified: hooks/apps/browser/computer-use disabled via --disable; effect only verifiable live
no problems found
```

## 5. Findings for review

**F1 — v1 sandbox does not bound Codex's other tools (Medium/High, pre-existing).**
`~/.codex/config.toml` enables MCP servers `idea` and `node_repl` and plugins
(`browser`, `computer-use`, …). v1 passes none of the `--disable`/`enabled=false`
overrides, so a bridge call can reach those tools. v1 also never passes approval policy
and omits `--model` on resume. v2 fixes all three. Whether to harden v1 or your global
Codex config now is a human decision; I did not touch either.

**F2 — v1 empty-question check was dead code.** It tested the composed prompt, never the
question. v2 validates the question itself (small intentional behaviour change).

**F3 — v1 blocks the MCP event loop.** Sync tool functions run inline in FastMCP 1.30, so
pings/cancellation were not serviced during a 15-minute turn. v2 uses async tools + a
worker thread.

**F4 — v1 env drift.** The v1 venv contains extra packages (`httpcore2`, `httpx2`,
`mcp-types`, `opentelemetry-api`, `truststore`) that a clean `mcp==1.30.0` install does not.
v2 is tested against the clean, hash-locked set only.

### Decisions requested from the human

| # | Decision | My recommendation |
|---|---|---|
| D1 | Accept that v2 switches off *all* Codex MCP servers/plugins/hooks/apps for bridge calls (GPT loses the IDE tools v1 exposed implicitly) | Accept; allow-list only servers you have verified read-only via `GPT_ARCHITECT_MCP_ALLOW` |
| D2 | Run the opt-in smoke test (2 real turns, disposable repo) before any activation | Yes — it is the only way to confirm F-unverified items below |
| D3 | Activation style: side-by-side `gpt-architect-v2` first, then cut over | Side-by-side |
| D4 | Whether to mitigate v1 (F1) before v2 is activated | Your call; v1 left as is |

## 6. Configuration precedence (summary)

Per tool, for model and effort independently:
`GPT_ARCHITECT_{MODEL,EFFORT}_{CONSULT,REVIEW}` → `GPT_ARCHITECT_{MODEL,EFFORT}` →
built-in `gpt-6.1-sol` / `high`. Validated against `codex debug models` before
inference. No escalation/fallback. Sessions keyed by repo × model × session name
(`GPT_ARCHITECT_SESSION`, default `default`). Full table in `README.md`.

## 7. Sanitized telemetry examples

Produced with the **fake** CLI (fixture numbers; `docs/telemetry_examples.jsonl` has all six):

```json
{"tool":"consult_architect","outcome":"success","queue_ms":0,"run_ms":105,"exit_code":0,
 "route":{"model":"gpt-6.1-sol","effort":"high","model_source":"default","effort_source":"default"},
 "session":{"name":"default","repo_key":"1811c1e0…","thread_id":"de374783-…","resumed":false},
 "usage":{"input_tokens":120,"cached_input_tokens":100,"output_tokens":30,"reasoning_output_tokens":10,"total_tokens":null},
 "error_category":null,"inference_may_have_started":true}
{"tool":"consult_architect","outcome":"success","usage":{"input_tokens":null,"cached_input_tokens":null,
 "output_tokens":null,"reasoning_output_tokens":null,"total_tokens":null},"…":"usage not reported -> all null"}
{"tool":"consult_architect","outcome":"error","error_category":"rate_limited","error_code":"rate_limited",
 "exit_code":1,"inference_may_have_started":true,"run_ms":106,"usage":{"…":"all null"}}
{"tool":"consult_architect","outcome":"error","error_category":"authentication","error_code":"auth_api_key",
 "inference_may_have_started":false,"run_ms":null,"exit_code":null}
{"tool":"consult_architect","outcome":"error","error_category":"invalid_input","error_code":"empty_input",
 "route":null,"queue_ms":null,"inference_may_have_started":false}
```

## 8. Install, smoke test, activation, rollback

See `README.md` (sections *Install*, *Live smoke test*, *Activation and rollback*). Short form:

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --no-deps -r requirements.lock.txt
.\.venv\Scripts\python.exe tools\verify_lock.py
# optional, needs your explicit approval (consumes allowance):
.\.venv\Scripts\python.exe tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance
# activation (human): claude mcp add --scope local --transport stdio gpt-architect-v2 -- <venv python> <architect_bridge.py>
# rollback:           claude mcp remove gpt-architect-v2 --scope local   (v1 untouched throughout)
```

## 9. Acceptance criteria status

| Requirement | Status | Notes |
|---|---|---|
| 1 Authentication | **Met** | status-only check, classification only, scrubbed API-key env; verified on real CLI + scenarios |
| 2 Read-only execution | **Partially met.** Flags/argv, approval policy and MCP set verified; tool inventory shows no IDE/browser/computer-use routes. **Sandbox write-blocking not live-verified** (shell could not start) | see §1a; blocker below |
| 3 Session management | **Met** | all listed behaviours tested, incl. multi-process; continuity confirmed live |
| 4 Model/effort routing | **Met** | catalog-validated; no fallback; route used live |
| 5 Telemetry | **Met** | real `usage` nesting confirmed live; `total_tokens` is null on this CLI |
| 6 Fault handling | **Met (fake child trees)** | not yet exercised against real Codex helper processes |
| 7 Dependencies | **Met** | hash-locked, clean-install proven, CLI series check |
| Verification (automated) | **Met** | 82 tests |
| Verification (live smoke) | **Executed; inconclusive on the key property** | write-block unproven; updated script cannot false-pass |

**Status update (supersedes the earlier blocker, see §1c/§1d/§1f):** with the sandbox
repaired and no `node_repl.exe` running, reads and continuity are demonstrated live, shell
write-blocking is demonstrated model-free, and `apply_patch` writes were *attempted by GPT*
and rejected in both fresh and resumed turns (stage B 13/13) with the OS layer verified
separately (stage A). **Remaining before activation:** decisions D1, D3, D8. The
pre-inference sandbox preflight now exists (§1e), so activating v2 while the ChatGPT app is
open fails loudly with `sandbox_unavailable` instead of returning an ungrounded answer.

## 10. Remaining limitations

See README *Known limitations*. Notably: Windows/py3.13/Codex 0.162.x only; no Windows job
object (hard-killing the bridge can orphan an in-flight Codex tree); pattern-based
detection of rate-limit/auth/missing-session text; hook discovery is heuristic; failed
first turns can leave an unreferenced Codex thread; named sessions are per MCP server
process, not per call (the two MCP signatures were fixed by the contract).
