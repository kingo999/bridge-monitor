# v2 remediation review package — for GPT (architecture) and the human

Prepared 2026-10-12. Implementer: Claude Code. **v2 is not registered, not activated, not
committed or published. v1 is untouched and live.** This pass authorised remediation only;
D1/D3/D8, activation and any v1 change remain with the human.

* Source under review: `C:\AI\gpt-claude-mcp-bridge-v2` — combined SHA-256
  **`e2273c3d98b4f8fd2904138f02743bf3feeefc23b30b736250a27dd078fbde0e`** (41 files,
  `docs/evidence/source_fingerprint.json`).
* Pre-remediation candidate (diff base): `.baseline_pre_remediation/`, combined SHA-256
  `cb02922b0c559a623b03430425f6d1c15daa43415aea5cc6efa1886f9bb4235f`.
* **No live model call was made in this pass.** All new results are mocked-CLI or model-free
  against the installed CLI.
* Results recorded for earlier candidates (smoke 16/16, attack 13/13, "read-only
  demonstrated") are **historical** (`docs/history/`) and are **not** evidence for this code.

## 1. Diff against the pre-remediation candidate

`docs/remediation.diff` (unified, source files) and `docs/remediation_stat.txt`. Summary:

| Area | Files | Nature |
|---|---|---|
| Process ownership (F3) | `bridge/jobobject.py` (new), `bridge/runner.py` | Windows Job Object ownership + termination states |
| Actual-turn failures (F1) | `bridge/runner.py`, `bridge/service.py` | item classifier, fail-closed/degraded |
| Capability policy (F2) | `bridge/codexcli.py`, `bridge/service.py` | strict schemas, resolved-state verification, delegation/image/web, exact version |
| State lifecycle (F4/F5) | `bridge/state.py`, `bridge/service.py`, `bridge/admin.py` | instance/loss, maintenance+quiescence, markers, journal, adopt/reconcile/reinit |
| Review consistency (F6) | `bridge/fingerprint.py` (new), `bridge/service.py` | before/after repository fingerprint |
| Attack-test validity (F7) | `tools/apply_patch_attack_test.py`, `tests/test_apply_patch_attack.py` | per-attack matching, specific evidence, thread identity |
| Evidence (F8) | `tools/record_evidence.py` (new), `docs/evidence/*`, this file, `README.md` | hash-bound records |
| Tests | `tests/test_remediation.py` (new, 48), fake CLI knobs, helpers | see §4 |

Remediation checklist (written before implementation): `docs/REMEDIATION_CHECKLIST.md`.

## 2. Finding-by-finding disposition

| # | Finding | Disposition | What is true now | What is NOT closed |
|---|---|---|---|---|
| **F1** | Actual-turn repo-access failures concealed by "completed + answer" | **Addressed** (fixture-verified) | Completed command/tool items that *report failure* with sandbox-setup / process-creation / repo-access signatures make the call fail (`sandbox_unavailable/turn_*`, answer withheld); explicit `degraded` opt-in returns a banner. Ordinary non-zero exits, agent narration and successful outputs containing the words are not flagged. Regression fixture present. Preflight gains a source-file read stage; docs say preflight ≠ exec and `git rev-parse` ≠ source readability. | Item shapes come from Codex's *rollout* records (`CommandExecution`, `aggregated_output`) and the binary's event names; the exact live `--json` nesting was **not re-captured** (§6). An unseen shape degrades to a false negative. The new source-read stage was **not exercised against the real CLI** (ChatGPT was running → lock). |
| **F2** | Capability containment; D1/D8 | **Partially addressed** | Allow-list empty by default; evidence levels *resolved/requested/uncontained*; strict `features`/`mcp` schemas; required controls and post-override readouts fail closed; **exact** version gate (0.162.1 only); delegation (`multi_agent`,`multi_agent_v2`), image generation (`image_generation`) and web (`web_search="disabled"`) addressed separately; allow-list exceptions in answer, telemetry and status; sandbox-backend override bannered. | Delegation/image removal *at runtime* and the `web_search` key's effect on `web__run` are **unverified** (no readout; needs a live tool listing). No web-exception mechanism exists (needs a human policy). D1/D8 pending. |
| **F3** | Process lifetime / session ownership | **Partially addressed** | Job Object (KILL_ON_JOB_CLOSE) owns the tree; termination is `confirmed`/`unconfirmed` from live-member accounting; descendants outliving the parent are reaped and recorded; unconfirmed ⇒ session blocked until `reconcile`; hard bridge kill reaps owned children (tested with a real kill) and the stale marker is auto-reconciled with a warning; recovery never kills anything; unrelated processes survive (tested). | Commands started by the Windows **sandbox service** are outside the job: their termination is unobservable and never claimed. Attachment follows `Popen` by ~ms. Hard-kill coverage is for job-owned members only. |
| **F4** | Registry lifecycle/admin | **Addressed** | `instance.json` + `registry_established`: loss ≠ first init (`registry_lost`, no replacement sessions); `registry.prev.json`; maintenance flag + lock-probe quiescence for quarantine/reinit/restore-prev; a late `bind_thread` cannot repopulate a lost/quarantined registry; instance-mismatch refusal; atomic writes and fixed lock order documented; scope of locks documented; reset retires pointers only. | Locks cannot coordinate with v1, manual `codex resume`, or the desktop apps (documented). First-init cannot be told from "both files deleted". |
| **F5** | Partial/uncertain session outcomes | **Addressed** | Explicit outcomes (`success_partial_persistence`, `success_session_in_doubt`, `degraded`, interrupted); pending marker blocks silent new threads until `adopt`/`reset`; journal keeps thread ids/call ids, no content; messages say "Codex history MAY contain a partial or complete turn in thread …"; partial success is bannered. | Journal/marker writes are best-effort (disk-full can lose them; the banner says so). |
| **F6** | Review consistency | **Addressed** | HEAD + status + diff-hash + untracked stat fingerprint before/after (`--no-optional-locks`, test proves `.git` untouched); change ⇒ banner + telemetry digests only; unavailable ⇒ reported; locks-don't-freeze-repo and side-by-side/pause-edits documented. | Same-size/same-mtime edits to *untracked* files are invisible; very large diffs may time out (reported as unavailable). |
| **F7** | Attack-test validity | **Addressed (code + oracle tests); live stages NOT run** | Each expected attack must be matched by exactly one call with specific policy/OS rejection evidence; exact counts and thread identity per turn; auth + exact-version gates before inference; labelled "production argv + runner, not the MCP/service path"; 24 oracle tests incl. missing / generic failure / duplicate / batched / wrong thread / changed files. | No live attack run for this code. The service path is not covered live. |
| **F8** | Evidence & docs | **Addressed** | New package; old one archived and labelled superseded; hash-bound evidence (tests, policy, fingerprint); classes in §3; unverified list in §6. | Live results for the new code do not exist (by instruction). |

## 3. Evidence classes (what supports what)

| Class | Items | Bound to |
|---|---|---|
| **Independently verified** (executed in this pass) | 162-test suite; effective capability policy read from the real CLI; v1/auth integrity manifest; lock verification; generated diffs | source hash `e2273c3d…de0e`, command, CLI 0.162.1, sandbox backend `elevated` |
| **Source observation** (read, not executed) | CLI help/schema facts: `features list --disable` reflects effective state; `--strict-config` unavailable on `features/mcp list`; `Config.web_search` enum; `multi_agent`/`image_generation` gates; event names; rollout record shapes | `docs/REMEDIATION_CHECKLIST.md`, `docs/evidence/capability_policy_real_cli.json` |
| **Historical report** (superseded; different code) | pre-remediation smoke 16/16, attack 13/13, sandbox-lock diagnosis, earlier telemetry | `docs/history/` |
| **Assumption** | listed in §6 | – |

Environment recorded with every result: Python 3.13.7, Windows 11 10.0.26300, Codex CLI
0.162.1 (node launcher), sandbox backend `elevated`; **`sandbox_provisioning_at_record_time:
failed`** — the machine's Codex sandbox refresh was failing while the ChatGPT app held
`node_repl.exe` (the known lock). That state is exactly why no live check was run now.

## 4. Test commands and results (actual)

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B tools\record_evidence.py fingerprint
.\.venv\Scripts\python.exe -B tools\record_evidence.py policy
.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label remediation_full
#  -> OK: 162 tests in 233.169s; failing=[]   (docs/evidence/tests_remediation_full.json)
.\.venv\Scripts\python.exe -B tools\verify_lock.py
#  -> environment matches requirements.lock.txt (30 packages)
.\.venv\Scripts\python.exe -B tools\v1_manifest.py compare docs\v1_baseline_before.json
```

* 162 tests, 0 failures, 0 skipped, no subscription calls (scripted fake CLI as a real child
  process; real file locks, real process trees, a real `TerminateProcess` of a job owner).
  Breakdown: `tests/test_remediation.py` 48 (F1 10, F2 9, F3 8, F4 9, F5 7, F6 5);
  `tests/test_apply_patch_attack.py` 24 (F7 oracle: 3 fixtures, 1 classification, 14
  negative-oracle, 6 plumbing); the other four modules 90 (config/argv/auth 18, state 18,
  service 43, server/admin 11) — the earlier suite, whose expectations were changed only where
  behaviour intentionally changed (exact-version gate, strict schemas, source-read stage,
  sandbox-override banner, real git repo in the test world; see the diff).
* **v1 integrity:** `v1_files`, `v1_listing`, `v1_state`, `v1_venv` unchanged. The manifest
  reports `codex_stat.config.toml` mtime changed (size identical, 3348 B); `auth.json`
  unchanged. Attribution: the file was rewritten 8 s after `ChatGPT.exe` started
  (14:57:23 → 14:57:31), the same pattern as the previous restart. Content equality cannot be
  proven (only size/mtime are recorded; credentials are never opened). I did not edit it.
* Tool limit: `record_evidence.py` records results; it does not prove completeness.

## 5. Updated threat boundary and the precise read-only claim

Bounded claim (README §Threat boundary): with CLI 0.162.1, a healthy sandbox and the
documented policy, the bridge pins `sandbox_mode="read-only"` + `approval_policy="never"` on
every fresh/resumed call, and the **tested native repository write routes (shell, `apply_patch`)
were rejected in recorded runs of earlier candidates**; for *this* source hash the pinning and
argv guards are test-verified and the live rejection evidence is **proposed, not re-executed**.

Not claimed: that all capabilities are read-only; that no external side effects occur;
repository confidentiality (content is sent to OpenAI by design); that the bridge writes
nothing outside the repository (bridge state, Codex history/sessions and sandbox ACL setup
are written elsewhere); anything for other CLI versions or an unavailable sandbox.

Threat boundary by component:

| Component | Constrained by | Not constrained |
|---|---|---|
| shell / patch tools | read-only sandbox (OS) + core approval policy | – (tested on prior candidates) |
| MCP servers, plugins, apps, hooks, browser/computer use | feature gates + per-server disables (resolved) | allow-listed servers; runtime effect unverified |
| delegation, image generation | feature gates (resolved) | runtime tool removal unverified |
| web | `web_search="disabled"` (requested) | any readout/verification; `web__run` mapping unknown |
| view_image, skills, goals | – | uncontained (not repo-write routes) |
| bridge/Codex state, sandbox ACLs | v2 state outside repo, journal | written by design |
| processes started by the Windows sandbox service | – | cannot be enumerated or confirmed terminated |

## 6. Remaining unverified (explicit)

1. Live tool inventory under the new policy (delegation/image/web actually absent?).
2. Whether `web_search="disabled"` removes `web__run`.
3. Live `--json` item shapes for failed commands (fixtures derive from rollout records).
4. The preflight source-read stage against the real CLI (blocked by the lock).
5. Attack tests, smoke test, and the MCP stdio/service path for **this** source hash.
6. Behaviour on the `unelevated` backend through `codex exec` after the lock fix.
7. Non-job-owned sandbox helper processes after timeout/cancel.
8. Any CLI version other than 0.162.1; non-Windows platforms.

## 7. Sanitized telemetry examples (fake CLI; fixture values; mocks force the uncertain cases)

Full set: `docs/telemetry_examples_remediation.jsonl`. Content-free by construction.

```json
{"_example":"F1 turn_sandbox_setup_failed","outcome":"error","error_category":"sandbox_unavailable",
 "error_code":"turn_sandbox_setup_failed","pointer_state":"unchanged","history":"turn_may_exist",
 "infra":{"command_items":1,"failed_items":1,"kinds":{"process_creation":1,"sandbox_setup":1}}}
{"_example":"F5 success_partial_persistence","outcome":"success_partial_persistence",
 "pointer_state":"pending_reconcile","session_state":"pending_reconcile","history":"turn_may_exist",
 "warnings":["state_not_saved:registry_unwritable"]}
{"_example":"F3 timeout, termination attempted but NOT confirmed","outcome":"timeout",
 "session_state":"in_doubt","process":{"ownership":"job_object","termination":"unconfirmed","active_after":1},
 "warnings":["child_termination_unconfirmed"]}
{"_example":"F3 refused","outcome":"error","error_category":"session_in_doubt",
 "error_code":"uncertain_termination","history":"no_turn_started","inference_may_have_started":false}
{"_example":"F6 repo changed during review","outcome":"success",
 "repo":{"changed":true,"head_changed":false,"before":"ae306ef507173f69","after":"2b559a1b00fe5560"},
 "warnings":["repo_changed_during_call"]}
{"_example":"F4 registry_lost","outcome":"error","error_category":"session_corrupt",
 "error_code":"registry_lost","history":"no_turn_started","inference_may_have_started":false}
```

## 8. Decisions (all pending; implementing a conservative candidate is not approval)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | Per-call disabling of all Codex MCP servers/plugins/hooks/apps/browser/computer use | (a) accept, empty allow-list; (b) allow specific read-only servers; (c) reject | (a); allow-list only after you verify a server is read-only. Allowed servers are bannered per answer |
| **D3** | Activation style | side-by-side `gpt-architect-v2` → cut-over; or cut-over directly | side-by-side, **after** the live checks in §9 pass for this hash |
| **D8-delegation/image** | `multi_agent`, `image_generation` gates | keep disabled (candidate default) / re-enable | keep disabled |
| **D8-web** | `web_search` | (a) keep disabled (default, no exception mechanism); (b) enable with a written egress/research policy (what may be queried; repository content never in external queries by default; logging) | (a). (b) needs a policy first; not implemented |
| **P2** *(paused design issue)* | Sandbox-service-spawned commands cannot be confirmed terminated | (a) accept and keep the documented limit; (b) require operator reconcile after *every* timeout/cancel; (c) investigate the Windows service's process model with OpenAI/Codex docs | (a) for the candidate; (c) before any production reliance. Evidence: process list showed `codex-windows-sandbox-service.exe` as a service |
| **P3** | Keep the opt-in `degraded` mode for actual-turn infra failures? | keep / remove | keep as opt-in; default `error` is fail-closed |

## 9. Proposed opt-in live checks and activation/rollback — **NOT EXECUTED**

Preconditions: ChatGPT/Codex apps closed, `Get-Process node_repl` empty, human authorisation
for allowance use. Each is bound to the hash it runs against.

1. `python tools\record_evidence.py fingerprint` (confirm hash unchanged since this package).
2. `bridge_admin.py status --sandbox` on a disposable repo — exercises the source-read stage.
3. `python tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance` — 2 turns:
   reads, continuity, telemetry, tool inventory for delegation/image/web (**new evidence for F2**).
4. `python tools\apply_patch_attack_test.py` (stage A), then `--live …` (stage B, 2 turns).
   Stage B covers production argv + runner, not the MCP/service path.
5. A service-path live check (not yet scripted): one `consult_architect` through
   `architect_bridge.py` over stdio against a disposable repo; assert the actual-turn
   inspection, fingerprint and telemetry fields on real output.

Activation (side-by-side) — human only:
```powershell
cd C:\dev\your-repo
claude mcp add --scope local --transport stdio gpt-architect-v2 -- `
  "C:\AI\gpt-claude-mcp-bridge-v2\.venv\Scripts\python.exe" "C:\AI\gpt-claude-mcp-bridge-v2\architect_bridge.py"
```
Identify the server by name in each request; pause implementation edits during reviews.
Cut-over: remove `gpt-architect`, add v2 under that name. **Rollback:**
`claude mcp remove gpt-architect-v2 --scope local` (re-add v1 with its README command if
removed). v1 state/history and Codex history are unaffected in every case; v2 state is only
`%USERPROFILE%\.gpt-architect-bridge-v2`.

## 10. Files

`REVIEW_PACKAGE.md` (this), `README.md` (spec/threat boundary), `docs/REMEDIATION_CHECKLIST.md`,
`docs/remediation.diff`, `docs/remediation_stat.txt`, `docs/evidence/*.json`,
`docs/telemetry_examples_remediation.jsonl`, `docs/history/` (superseded material),
`.baseline_pre_remediation/` (diff base).
