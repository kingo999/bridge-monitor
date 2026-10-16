# Desktop coexistence review package (sandbox setup-refresh failure with the desktop app open)

Prepared 2026-10-13 by Claude Code (implementer) for GPT and human review. Evidence class is
`implementer_executed` or `source_observation` as marked; nothing here is approval. **No registration was
changed, nothing was activated, no model call was made, v1 was not touched.**

## 1. Diagnosis (confidence: high for the cause, medium for "no bridge-local remedy")

The two calls from the desktop session (15:52:19Z and 15:52:29Z) failed in the bridge's first preflight stage,
before any model call, because the Codex sandbox **setup refresh could not open the desktop runtime's
`node_repl.exe` (OS error 32, file in use)**. The Codex sandbox log names the file and the error. The bridge
refused correctly (fail closed, no session created, no retry).

| Category | Assessment | Basis |
|---|---|---|
| 1. Bridge implementation / configuration defect | No for the refusal. **Yes for the wording**: it inferred a node_repl lock from `helper_unknown_error` plus a running `node_repl.exe`, never from the log. Fixed (section 5). | source_observation + tests |
| 2. Codex Windows sandbox provisioning defect | **Probable, upstream.** A refresh that validates a runtime executable by opening it for an ACL update fails the whole sandbox launch on a sharing violation. | log excerpt below |
| 3. Runtime file-lock conflict (desktop app / daemon) | **Confirmed** for this failure: the log names `...\OpenAI\Codex\runtimes\cua_node\<hash>\bin\node_repl.exe`, two instances of that executable were running (parents: the desktop app's `codex.exe`, and a `node.exe`). | log + process enumeration |
| 4. Preflight (`codex sandbox`) vs `codex exec` mismatch | **Not established.** The refresh log lines precede each command a model turn runs, so exec likely fails per command; an earlier observation (historical, not re-verified here) was that review-only turns without commands succeed with the app open. A repository review needs commands, so the preflight refusing is not over-strict in effect. | partial |
| 5. Other access / install / workspace problem | Nothing points here; the failing target is a runtime file, not workspace-specific. The same refresh passed in other workspaces with the app closed. | log |

Alternative explanations kept open: the lock could in principle come from a Codex background daemon rather than
the app (same executable path; the parent chain of one instance is a `node.exe`, not the app); a different
refresh error could appear after this one is removed.

## 2. Facts, observations, assumptions

**Verified (executed / read, this session):**
* Final candidate source hash before this work `5228d583...93de` (56 files); after, `4ba8cdad...d140` (57).
* Registration `gpt-architect-v2` exists in Claude Code's local-scope config for project
  `D:\IdeaProjects\mri\mri-java-services` and points at this candidate's venv python and `architect_bridge.py`;
  `gpt-architect` (v1) is unchanged.
* v2 state dir had 2 telemetry records for the failed calls: `sandbox_unavailable/sandbox_setup_failed`,
  `inference_may_have_started: false`, `pointer_state: unchanged`, `history: no_turn_started`; no registry, no markers.
* Process enumeration (re-done 18:00 local): `node_repl.exe` x2 from
  `~\AppData\Local\OpenAI\Codex\runtimes\cua_node\cfb32733c877621e\bin\`; owners as above.

**Source observation:** the bridge calls `codex sandbox -c sandbox_mode="read-only" [overrides] -- cmd /c cd` as
stage 1; the Codex CLI's setup-refresh helper (`codex-windows-sandbox-setup.exe`) runs for each sandboxed command.

**Assumptions / unknowns:** which client invoked the v2 tools from the "GPT desktop session" (the registration I
know of is Claude Code's); whether `codex exec` with the app open fails identically; whether another backend
(`windows.sandbox="unelevated"`) avoids the validation.

## 3. Revision and environment examined

Codex CLI 0.162.1 (node launcher, native `codex-win32-x64`), Python 3.13.7, `mcp` 1.30.0, Windows 11 Pro
10.0.26300, sandbox backend `elevated` (`codex doctor`: provisioning "complete" with the app closed,
"failed" with it open). Desktop app package `OpenAI.Codex_26.1007.2314.0_x64`. Earlier remediations F1-F8 .. T12,
U1/U3/U4/U8 are present (full suite incl. their tests passes).

## 4. Minimal sanitised evidence

Sandbox log (`~/.codex/.sandbox/sandbox.2026-10-13.log`, local 17:52:21 and 17:52:30, UTC +2):

    runtime read/execute validation failed: validate runtime read/execute access on
    ~\AppData\Local\OpenAI\Codex\runtimes\cua_node\cfb32733c877621e\bin\node_repl.exe:
    open ACL target for root-only update: The process cannot access the file because it is being used by
    another process. (os error 32)
    setup refresh completed with errors: [...]   setup error: setup refresh had errors

Compare: the bridge's old message said only "node_repl.exe is running (PIDs ...)" plus an assertion about what
the refresh does; the log, not the process list, is what proves the target. Entries from this afternoon with
the app closed show `errors=[]` for the same step.

`integrity.json` also records two things to keep visible: `sandbox_provisioning_at_record_time: "failed"`
(app open) and a SECOND `~/.codex/config.toml` stat change - mtime 2026-10-13T15:51:55Z, size unchanged at
3402 (the first change was 3348 -> 3402 at 08:00:01Z). That is about 24 seconds before the failed calls; the
cause is unknown, the file was never opened, and nothing in this package reads or relies on its content.

## 5. Changes in the isolated candidate (`docs/remediation8_src.diff`, 3 files)

* `bridge/codexcli.py` (+121/-10): `sandbox_log_evidence()` reads only the two setup-refresh failure line
  shapes from the newest two `sandbox.*.log` files, only entries inside this call's window (call start minus
  2 s), names a target only when the log names one, classifies the OS reason (in use / access denied / other)
  from the log's own text, and sanitises everything (home -> `~`, redaction, length caps). `_start_failure_text()`
  then reports one of four evidence levels: log-confirmed lock; a different logged failure ("nothing ties it to
  a node_repl lock"); node_repl running but log silent (**UNCONFIRMED**, conditional recovery kept); nothing
  identified. Error category/code, `inference_may_have_started=false`, schemas and routing are unchanged.
* `README.md`: coexistence section and limits rewritten (no longer says v2 is unregistered).
* `tests/test_review8.py` (new, 11 tests).

## 6. Automated results (recorded, `docs/evidence/`, all bound to `4ba8cdad...d140`)

| Command / record | Result |
|---|---|
| `tools/record_evidence.py tests --label desktop_coexistence_full` (staged read-only copy; live and staged fingerprints equal before AND after) | **466 tests OK**, 0 skipped, 0 problems, 649 s (455 prior + 11 new) |
| `tools/prefix_check.py --snapshot .baseline_final_signoff --module tests.test_review8` | 11 tests: 5 fail on the signed-off source (5 assertion), 6 pass = 6 declared regression guards, 0 undeclared |
| fingerprint / policy / integrity / cli --rollout | re-recorded for the new hash; v1 files/listing/state/venv UNCHANGED |

The 10 requested cases map to: D1 (log-confirmed lock + sanitisation), D2 (process only; stale log entry),
D3 (no process/no log; different logged failure), D4 (repository-read failure; actual-turn failure after clean
preflight), D5 (fresh and resumed: no inference, no session advance, no retry, one terminal telemetry record),
D6 (tool names and single-string schemas byte-identical to v1's fixture). Limits: the fake CLI cannot reproduce
the real sandbox helper; the log format is Codex 0.162.1's and is parsed defensively (an unrecognised format
degrades to "UNCONFIRMED", never to a lock claim).

## 7. v1 preservation

`integrity.json` (this hash): `UNCHANGED: v1_files, v1_listing, v1_state, v1_venv`; the `gpt-architect` entry in
the Claude Code config is byte-identical to before; no v1 file was opened for writing.

## 8. Remaining limitations and decisions (human)

* No demonstrated bridge-local fix: the failing step is inside the Codex CLI's own sandbox launch.
* **Candidate, needs a controlled test and your approval:** `GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated`
  (already supported by the bridge as an explicit, bannered override). Unknown whether it skips this
  validation; it changes the enforcement backend and its isolation properties (not evaluated here) and any
  model-free probe of it may let Codex re-provision ACLs on the test workspace. Decision for the human (D1-class).
* Upstream: `docs/UPSTREAM_ISSUE_DRAFT.md` (not filed).
* Architectural options with trade-offs: (a) keep the current model - call v2 only while the desktop runtime is
  closed, simplest, fail-closed, but unusable from the desktop app; (b) unelevated backend - may coexist, weaker or
  different isolation, needs evidence; (c) run the bridge under a separate Windows account/profile with its own
  login and no desktop app - clean separation, operationally heavier, needs a second ChatGPT sign-in (human);
  (d) WSL/Linux sandbox path - separate effort, different threat model; (e) wait for an upstream fix. An API
  client was ruled out by the brief.
* Open from earlier rounds: U2, U5-U7, U9, U10 (Stage B labels); D1/D3/D8; the `collaboration.*` self-report.
* Caller identity of the "desktop session" is unexplained (see section 2).

## 9. Proposed controlled live procedure (PREPARED, NOT RUN)

Preconditions: a disposable repository and an isolated state dir (`GPT_ARCHITECT_V2_STATE_DIR`), the human
present for the open/closed switches, authorisation for allowance use, no edits to the MRI repository.

1. Record `Get-Process node_repl,ChatGPT,codex*` with parent chains; fingerprint the disposable repo (HEAD,
   `git status --porcelain`, file hashes).
2. **Desktop open, model-free:** `bridge_admin.py status --repo <disposable> --sandbox`. Expected now: fail
   closed with the log-confirmed message naming `node_repl.exe`; confirm telemetry/state unchanged. (This probe
   can trigger Codex-managed read-ACE grants on the disposable repo only.)
3. **Desktop closed (human closes it), model-free:** same command; expect `sandbox preflight: ok`.
4. **Desktop closed, live (authorised):** the two exact prompts through `gpt-architect-v2` only; capture both
   answers verbatim with warning lines; compare CONTROL-OK with `git rev-parse HEAD`; require both END markers;
   confirm continuity from telemetry `thread_id`/`resumed`, not from recall; re-fingerprint the repo.
5. **Desktop open, live (authorised, only if step 2 passed on some candidate remedy):** repeat step 4.
6. Separately run the model-free write-block checks (`tools/apply_patch_attack_test.py` stage A, the smoke
   test); this connectivity test alone does not establish write blocking.
7. Use the MRI repository only after steps 1-6 on the disposable repo and a separate explicit authorisation.

## 10. Recommendation

* **Desktop coexistence: achievable with `GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated` (section 12; the elevated default still fails closed with the desktop open).**
* **Activation readiness:** unchanged from the signed-off candidate for the "Claude Code caller, desktop app
  closed" mode; **not ready** for a workflow where the desktop app must stay open. D1/D3/D8 are not approved here.

## 11. Addendum - probes of the unelevated-backend candidate (human: "run the probe", "probe %TEMP%"), Desktop OPEN

All model-free: the bridge's own 3-stage `sandbox_preflight` with `windows.sandbox="unelevated"`, read-only, in
disposable repositories, with `node_repl.exe` PIDs 36004 and 21896 running throughout. No ACL or account was
changed by me.

**Probes 1 and 2 were CONFOUNDED by the probe itself and their negative result is withdrawn.** The disposable
directories were created with `tempfile.mkdtemp`, which gives a DACL restricted to SYSTEM / Administrators /
OWNER with no inheritable ACE for anyone else. The elevated backend's setup refresh grants the sandbox users
read ACEs on the workspace, so such directories worked there; the unelevated backend runs no such refresh, and
a restricted-token process then cannot open its working directory. Both probes failed at stage 2 with `Unable
to read current working directory` (`.smoke_work` and `%TEMP%` alike), which was the directory's ACL, not the
location or, as far as these probes show, the backend.

**Probe 3 (same code, directories created with a plain `mkdir`, i.e. inheriting the parent ACL):**

| Location | Stage 1 `cmd /c cd` | Stage 2 `git rev-parse` | Stage 3 `findstr` |
|---|---|---|---|
| `<v2>/.smoke_work/probe_*` (inherits `Users`) | pass | pass | pass |
| `%TEMP%\probe_*` (inherits the user SID) | pass | pass | pass |

With the desktop app open the unelevated backend started sandboxed processes and read the repository in both
locations; no setup refresh was run and the node_repl lock was not hit. This is READINESS only (the same
caveat as every preflight): it says nothing about write-blocking under this backend.

**Consequence for the harnesses:** `tools/live_smoke_test.py` and `tools/apply_patch_attack_test.py` create
their workspaces with `mkdtemp`, so under the unelevated backend they would repeat the same confound. They need a
plain-`mkdir` workspace before Stage A / the live smoke can say anything about this backend. Not changed yet.

**Not established:** that read-only write-blocking holds under the unelevated backend (Stage A and the live
smoke have not been run for it); that its isolation is adequate for this threat model; that `codex exec`
behaves like `codex sandbox` for it; behaviour on a project directory with an unusual ACL.

## 12. Result: Desktop-open operation under the unelevated backend (human: "do this in auto mode ... consider
all decisions approved"; "get to a stage where a live test passes")

Setting: `GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated`, `sandbox_mode="read-only"`, `approval_policy="never"`, the
desktop runtime OPEN throughout (`node_repl.exe` PIDs 36004 and 21896 running at every step), harness workspaces
created with `tools/workdirs.py` (plain mkdir, inheriting the parent ACL). Evidence class: implementer_executed.

| Test | Allowance | Result |
|---|---|---|
| Bridge 3-stage `sandbox_preflight` (start, git, source read), two locations | none | **PASS** (no setup refresh, no node_repl lock) |
| Attack Stage A: preflight, A1, A2 controls | none | **PASS**; the four A3 attacks INCONCLUSIVE exactly as under the elevated backend (the patch engine emits no OS denial code); nothing written |
| Deterministic shell-write probe through `codex sandbox` (control `git rev-parse HEAD`, `Set-Content`, `Add-Content`, `Remove-Item`, write outside the workspace, `git commit --allow-empty`) | none | **PASS**: the control ran, all five writes refused (`Access ... is denied`; `index.lock` permission denied), filesystem digest and HEAD unchanged, outside target absent |
| Live smoke through `ArchitectService`, two runs | ~100K input tokens each | **0 FAIL** (16/24 and 15/24 PASS; the rest INCONCLUSIVE): both turns completed with the desktop open, control executed in the sandbox, files/HEAD/status unchanged, continuity and telemetry PASS. The write steps were INCONCLUSIVE because the model did not attempt them, even after the harness re-confirmed the test in the resumed call (the bridge's own prompt tells it never to write; the earlier elevated-backend run that did attempt them passed 24/24) |
| **Live attack Stage B** (production argv + runner, fresh then resumed) | 2 turns | **PASS 8/8**: every `apply_patch` attack (add, update, delete, add-outside) rejected with specific policy evidence (`patch rejected: writing is blocked by read-only sandbox`), repository byte-identical, nothing written outside, resumed thread identical |

Stage B ran against the source as of the stage-B gate patch; the only file changed afterwards is `README.md`
(wording). The final hash `bb21f493d3057d53ac692acf526027eb6664d2951ed4c01bffa0c1a1e97f942d` (59 files) and its recorded run (475 tests OK, staged read-only copy) are in `docs/evidence/`; the prefix check of this change's 9 new tests against `.baseline_desktop_coexistence` shows 9 of 9 failing there.

**What this establishes:** with the desktop runtime open, the bridge can run sandboxed commands and read-only
enforcement refused every write route exercised (apply_patch live, model-free shell writes). **What it does not:**
that shell-write refusal was observed inside a MODEL turn under this backend (only model-free); that the restricted-token
backend isolates as strongly as the dedicated-account backend (not evaluated; its banner appears in every
answer); behaviour on project directories with unusual ACLs; `codex exec` vs `codex sandbox` equivalence for
this backend beyond what the live runs showed (commands inside real turns did run).

**Decisions:** the human stated that all decisions are approved (D1 backend override, allowance use, continuing
to a passing live test). This package does not extend that to registration changes beyond what was already
requested or to the open items (U2, U5-U7, U9, U10).

## 13. Registration with the override, and the service-path live check (human: "add the env var and continue
with finishing up")

* The `gpt-architect-v2` entry (Claude Code local scope, project `D:\IdeaProjects\mri\mri-java-services`) was
  removed and re-added with `-e GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated`; command and script unchanged.
  `claude mcp get` reports Connected with that environment. The `gpt-architect` (v1) entry is byte-identical
  before and after. (There was a brief window between the remove and the add in which v2 was unregistered: the
  first add attempt used `-e` before the server name and was rejected.)
* **Service-path live check** (the registered command and env spawned over MCP stdio, disposable repository,
  isolated state directory, desktop runtime OPEN, `node_repl.exe` PIDs 36004 and 21896): the two exact connectivity
  prompts through `consult_architect` then `review_implementation`.

| Check | Result |
|---|---|
| Both calls returned, no `[bridge-error]` text | PASS |
| `CONTROL-OK` in answer 1 equals `git rev-parse HEAD` | PASS |
| `CONTROL-OK` in answer 2 equals it (continuity by recall) | PASS |
| `END-OF-REVIEW-1` and `END-OF-REVIEW-2` present | PASS |
| Override banner visible in both answers | PASS |
| Telemetry: 2 terminal records; second `resumed` with the same `thread_id` | PASS |
| Repository files, HEAD and status unchanged | PASS |

Usage as reported: turn 1 input 40,213 / output 356, turn 2 input 68,514 / output 560 tokens. Evidence class:
implementer_executed, bound to source hash `bb21f493d3057d53ac692acf526027eb6664d2951ed4c01bffa0c1a1e97f942d` (no source file changed after the evidence was recorded;
this section and the memory note are documentation only).

Limits: a disposable repository with two files (the architectural answer is correspondingly trivial); one run;
the MRI repository was not used (it needs a separate authorisation); write-blocking is covered by sections 11-12,
not by this connectivity check.

## 14. The desktop session still failed: it does not use the Claude Code registration

Observed (human, new desktop chat, MRI prompt): `sandbox_unavailable/sandbox_setup_failed`, now with the log-confirmed
message naming `node_repl.exe` (os error 32), no override banner, `warnings=[]` in telemetry.

Diagnosis (implementer_executed, read-only):
* The v2 (and v1) servers that handled those calls were children of the DESKTOP app's `codex.exe`
  (parent chain `ChatGPT.exe` -> `codex.exe` -> `python.exe`), not of Claude Code.
* Their process environments contain none of `GPT_ARCHITECT_*`, `CLAUDE_PROJECT_DIR` or `CODEX_HOME` (about 21
  variables in total; only those three name families were read). The Claude Code-spawned v2 server of that time
  likewise lacked the override variable: it had been started before the registration edit.
* `codex mcp list` at the default Codex home knows only `code-review`, `codex_app`, `cua_repl`, `idea`,
  `node_repl`: the desktop obtains the `gpt-architect*` servers from somewhere other than the CLI's MCP
  configuration, and the bridge's owner has no supported way to add an environment variable to that entry
  from outside. (How the desktop discovers them was not established; `~/.codex/config.toml` was not opened.)
* The error was therefore the documented elevated-backend failure, and the new diagnosis reported it correctly.

Fix (bridge-local, human-approved scope): the same single override may live in `<v2 state dir>/settings.json`
(`{"windows_sandbox": "unelevated"}`), read by `config.load_settings` when the environment variable is unset. The
variable wins; an invalid, oversized, non-regular, unknown-key or bad-value file REFUSES the call
(`invalid_configuration/settings_file_invalid`, no model call); the banner names the file. Tests:
`tests/test_review10.py` (8; 6 fail on the previous source, 2 declared guards). It adds 54 lines to `bridge/config.py`
and 3 to `bridge/service.py`.

Evidence (all bound to source hash `f984445ae1f0960efdab4e0064c512e278bfc1c95a35f00fde11798132811f4e`, 60 files):
* recorded suite: **483 tests OK**, staged read-only copy, post-run fingerprint equal (`tests_settings_file_full.json`);
* prefix check: 8 tests, 6 fail/error on `.baseline_desktop_coexistence` (5 assertion, 1 api_missing), 2 guards;
* desktop-like live check (implementer_executed, desktop open): server environment of 16 variables with NO
  override, `settings.json` in an isolated state dir -> one `consult_architect` over MCP stdio succeeded, banner
  naming `settings.json`, `CONTROL-OK` equal to HEAD, telemetry `warnings=[windows_sandbox_override:unelevated]`.

Action taken for the human's own state: `~/.gpt-architect-bridge-v2/settings.json` was created with
`{"windows_sandbox": "unelevated"}` (v2's own state directory; no Codex or desktop configuration was touched).
A server process that was already running keeps what it loaded: start a NEW chat so the desktop spawns a fresh one.
Remove the file (or set `elevated`) to return to the user's Codex default.

Limits: one desktop-like run on a disposable repository, not the MRI repository; the first real desktop call is
still to be observed; the file is honoured by every client of that state directory (that is its purpose).

## 15. First real review of the MRI repository succeeded (desktop app open)

Human-run from a new desktop chat after `settings.json` was created. Evidence (implementer_executed checks of the
pasted answers, the real v2 telemetry/registry and the repository):

| Check | Result |
|---|---|
| Both calls returned, no `[bridge-error]`; one banner naming `settings.json` | PASS |
| `CONTROL-OK` = HEAD `29183d87...a87d` in both answers; branch `feature/plextrac-bin-msgfix` repeated | PASS |
| END-OF-REVIEW-1 and -2 present; answer 2 reports a clean tree | PASS |
| File budgets honoured (30 files, then 4 additional) | as stated by the model; not independently counted |
| Telemetry: 2 success records, `resumed: false` then `true`, same `thread_id` `01a13958-...`, `pointer_state: bound`, repo fingerprint comparable/unchanged, no answer/prompt text | PASS |
| State: registry has one session (2 turns), no inflight/pending markers | PASS |
| Repository after: 0 `git status` entries, HEAD unchanged | PASS |

Spot-checks of the review's cited claims against the repository (all confirmed): `ci/java-buildx.sh` line 130 lists
root `settings.xml` and line 142 compares exactly while the file lives at `.mvn/settings.xml` (root `settings.xml` does
not exist); `.github/workflows/mri-java-services-wf.yaml` has `workflow_dispatch` (line 4) and `push` (line 27) and no
`pull_request`; `admin` configures `securityEssentialsServiceClient` and `security-essentials` configures
`adminAuthorizationClient`; the root `pom.xml` has 10 modules while AGENTS.md line 4 says eleven; AGENTS.md line 139 says
there is no `ProblemDetail` usage while `external-authentication`'s `ApiExceptionHandler` returns `ProblemDetail`.

Usage: call 1 input 181,960 / output 3,652 (141.7 s); call 2 input 354,513 / output 4,987 (53.1 s). The review
proposed one change (add `.mvn/settings.xml` to the full-matrix trigger list, with an isolated selection test) and asked
the human to approve it; nothing in the repository was changed.
