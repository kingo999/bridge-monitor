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

* **Desktop coexistence: not achievable with this CLI/backend today, and the bridge should keep failing closed.**
  Use v2 from Claude Code with the desktop runtime closed, or pursue the unelevated-backend test / upstream fix.
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
