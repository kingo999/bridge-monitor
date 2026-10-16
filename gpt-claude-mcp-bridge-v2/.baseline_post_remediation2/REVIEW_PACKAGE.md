# v2 second-remediation review package — for GPT (verification) and the human

Prepared 2026-10-12. Implementer: Claude Code. **v2 is not registered, not activated, not
committed or published. v1 is untouched and live.** This pass authorised remediation of GPT's
findings N1–N15 and the P3 change, then *preparation* (not sending) of a verification prompt.
D1/D3/D8, activation, registration and any v1 change remain with the human.

* Source under review: `C:\AI\gpt-claude-mcp-bridge-v2` — combined SHA-256
  **`07106480c5492d99030acdd14d79bc2ce64d81ae80610ad39d601f13e1e2d373`** (45 files,
  `docs/evidence/source_fingerprint.json`).
* State GPT reviewed in round 1 (diff base): `.baseline_post_remediation1/`
  (combined hash of all files `8c71495b…6f62`; evidence hash at review `e2273c3d…de0e`).
* Original pre-remediation candidate (cumulative diff base): `.baseline_pre_remediation/`
  (`cb02922b…4235f`).
* **No live model call was made to produce the evidence in this package.** All results are
  mocked-CLI or model-free against the installed CLI (`codex features list`, `codex mcp list`,
  `codex doctor`, `codex debug models`; no inference, no sandbox preflight). **One
  human-authorised model call was made afterwards: GPT's verification review of this source
  (§9)**; it is advisory and is not evidence about the bridge's behaviour.
* Results recorded for earlier candidates (smoke 16/16, attack stage A 5/5 and B 13/13,
  "read-only demonstrated") are **historical** (`docs/history/`) and are **not** evidence for
  this code. Evidence records for the first remediation are archived in
  `docs/history/evidence_remediation1/` and are superseded.
* Who produced the evidence: every result below was executed by the implementer (Claude Code)
  on the human's machine. The class is `implementer_executed`, deliberately not "independently
  verified". A reviewer cannot confirm the totals from the record alone.

## 1. What changed since GPT's review

Diff: `docs/remediation2.diff` + `docs/remediation2_stat.txt` (vs the state GPT reviewed);
cumulative `docs/remediation.diff` + `docs/remediation_stat.txt` (vs the original candidate).
Checklists written before implementation: `docs/REMEDIATION_CHECKLIST.md`,
`docs/REMEDIATION_CHECKLIST_2.md`.

| Area | Files | Nature |
|---|---|---|
| Host git containment (N1) | `bridge/gitsafe.py` (new), `bridge/fingerprint.py`, `bridge/codexcli.py` | one place builds host git argv/env; attr-source/fsmonitor/textconv/ext-diff controls |
| State validation (N2) | `bridge/admin.py`, `bridge/state.py` | `validate_state_dir` before every admin state access; rejected dir never probed |
| Ownership (N3/N4) | `bridge/jobobject.py`, `bridge/runner.py`, `bridge/service.py`, `bridge/state.py` | suspended start → job → resume; refusal before prompt; exception-safe lifecycle; owner-identity stale recovery; P2 reconcile; exit-accounting grace |
| Maintenance (N5) | `bridge/state.py`, `bridge/admin.py`, `bridge/service.py` | exclusive owner-stamped maintenance; session commands refuse during it |
| Durable admission (N6) | `bridge/service.py`, `bridge/state.py` | in-flight marker required; retained until pointer commit; `completed_unbound`; instance errors converted |
| Infra evidence (N7) | `bridge/runner.py`, `bridge/service.py` | error events, full text, oversize lines, wider repo-access signatures |
| Capability drift (N8) | `bridge/codexcli.py`, `bridge/service.py` | final-readout exceptions, pre-exec/post-turn recheck, duplicate rows rejected |
| Attack oracle (N9/N12) | `tools/apply_patch_attack_test.py`, `tests/test_apply_patch_attack.py` | wrapper-attested, exact patch, OS-denial code required |
| Instance identity (N10) | `bridge/state.py` | absent/unreadable/invalid/ok; validated restore |
| Fingerprint (N11/N15) | `bridge/fingerprint.py`, `bridge/service.py` | cached vs worktree, unborn-safe, truncation ⇒ unavailable, bannered |
| Preflight probe (N13) | `bridge/codexcli.py` | `findstr`, unsafe names excluded, visible skip |
| Identifier ingress (N14) | `bridge/ids.py` (new), `bridge/runner.py`, `bridge/telemetry.py` | validate at ingress; nested telemetry fixed formats |
| P3 | `bridge/config.py`, `bridge/service.py` | `degraded` removed; flag line prepended |
| Evidence/tooling | `tools/record_evidence.py`, `tools/make_verification_prompt.py` (new), diff tools, telemetry examples | `implementer_executed`; prompt refuses stale evidence |
| Tests | `tests/test_review2.py` (new), fake CLI knob `mcp_sequence`, updated expectations | see §4 |

Also changed, not requested (check as potential regressions): suspended start and the 0.5 s
exit-accounting grace (found through a flaky test: job accounting still shows members for
~4 ms after a normal exit); `_run_capture` reads its cap at call time; `classify_item` uses a
path-boundary containment match; stage A additionally requires an OS access-denial code;
`spawn_failed` is treated as "nothing ran" (it previously marked the session uncertain);
`process_identity`/`owner_is_alive` no longer read an access-denied query as owner death;
`registry_identity_unverifiable` has actionable recovery text. The last three were found by
re-reading the new code while preparing this package, not by GPT.

## 2. Finding-by-finding disposition

Vocabulary: **Implemented + tested (mocked)** = the code does it and an automated test
exercises it; it is *not* "closed" until GPT or the human confirms. **Documented limit** =
not eliminated, stated plainly.

| # | Finding | Disposition | What is true now | What is NOT closed |
|---|---|---|---|---|
| **N1** | Host git helpers | Implemented + tested (mocked) | All host git via `gitsafe`; control proves plain git fires textconv/clean/fsmonitor helpers and nothing fires through the bridge; mutation test; recursive `.git` snapshot | `$GIT_DIR/info/attributes` is still read by git; a git bug is out of scope; preflight `codex sandbox` is a different route (see N13) |
| **N2** | Admin bypasses state-dir validation | Implemented + tested | every admin command validates; rejected dir is never created/locked/probed | none known beyond what tests cover |
| **N3** | Ownership gaps | Implemented + tested; **documented limit** | suspended→job→resume; no ownership ⇒ child killed before any prompt byte; P2: timeout/cancel on Windows ⇒ in doubt until `reconcile` | service-spawned commands are still unobservable (the operator is the confirming party); live behaviour of suspended start with the real `codex` launcher is not exercised (fake CLI only) |
| **N4** | Setup exceptions / stale recovery | Implemented + tested | lifecycle exception-safe; auto-clear only if owner (pid + creation time) provably dead and job-owned and not `completed_unbound` | identity relies on the process creation time; an owner that exists but cannot be queried counts as alive (an earlier draft read a failed query as death — found while preparing this package and fixed, with a regression test); an exited process is dead even if a handle to it is open |
| **N5** | Maintenance ownership | Implemented + tested (real threads) | exclusive lock + owner-stamped flag; owner-only clear; session commands/calls refuse | stale flag needs an operator (`end-maintenance`) |
| **N6** | Best-effort markers / early clearing | Implemented + tested | in-flight marker mandatory; retained until pointer commit; pending/uncertain write failures keep blocking; instance errors converted; truthful banners | disk-full during *journal* write can still lose a journal line (journal is advisory, not admission) |
| **N7** | Infra failure with ordinary success | Implemented + tested | error events, full item text, oversize lines classified; ordinary non-zero exits not flagged | live `--json` shapes not re-captured; unseen shapes ⇒ false negative |
| **N8** | Capability snapshot | **Documented limit** + narrowed | exceptions from final readout; recheck before exec and after turn; duplicates rejected; `uncontained` | **no supported MCP deny-all**; a server added after the final recheck is not contained |
| **N9** | Wrapper output as evidence | **Documented limit** | static wrapper checks + exact patch + label `wrapper_attested` | Codex persists no nested records; wrapper-attested is the strongest available evidence on 0.162.1 |
| **N10** | Instance identity fail-open | Implemented + tested | absent/unreadable/invalid/ok; backup validated on restore | no automatic legacy migration: a registry without a verifiable identity is refused and needs operator action (`quarantine`, `reinit`, `adopt`) |
| **N11** | Fingerprint gaps | Implemented + tested | separate cached/worktree streams; unborn; truncation ⇒ unavailable | same-size/same-mtime untracked edits invisible |
| **N12** | Generic failures / stage A | Implemented + tested (oracle only) | no generic wording; per-hunk runs; timeout/launch/unattributable ⇒ INCONCLUSIVE; OS denial code required | **no live run** for this source; if the real engine's text lacks a denial code stage A reports INCONCLUSIVE |
| **N13** | Filename reparsed by `cmd` | Implemented + tested | `findstr` with name as data; unsafe names excluded; visible skip; source extension preferred | probe is one tracked file, not proof of general readability |
| **N14** | Unvalidated ids | Implemented + tested | validated at ingress; nested telemetry strings fixed formats | `redact.py` is secret-pattern only: error excerpts to the caller may include paths/source (never telemetry) |
| **N15** | Hidden degradations | Implemented + tested | flag line + detail in the answer, plus warnings | – |
| **P3** | Degraded mode | Implemented on the human's instruction | `degraded` removed; env value rejected; infra failure ⇒ answer withheld | the decision remains revisable by the human |

### F1–F8 (GPT's "Remaining" items → where they landed)

| # | GPT's remaining | Now |
|---|---|---|
| F1 | errors outside completed items; oversize; pre-completion `error`; live shapes | N7 implemented; live shapes still unverified |
| F2 | runtime effects unverified; config change between audit and exec | N8: narrowed + documented; D1/D8 pending |
| F3 | un-owned normal exit; assignment window; setup exceptions; stale recovery; service children | N3/N4 implemented; service children = documented limit with P2 default |
| F4 | maintenance ownership; session mutations; instance validation; state-dir validation | N5/N10/N2 implemented |
| F5 | marker failures; crash window; overclaiming banners | N6 implemented |
| F6 | staged content; unborn; unavailable not bannered; host git helpers | N11/N15/N1 implemented |
| F7 | wrapper evidence; generic OS regex; stage A on timeout | N9 (label) / N12 implemented; no live run |
| F8 | attribution; overbroad claims; rollback wording; section refs | this package and README; §3 wording applied |

## 3. Evidence classes (what supports what)

| Class | Items | Bound to |
|---|---|---|
| **implementer_executed** | 238-test suite; effective capability policy read from the real CLI; v1/auth integrity manifest; lock verification; generated diffs | source hash `07106480…d373`, command, CLI 0.162.1, sandbox backend recorded in each record |
| **source_observation** (read, not executed) | CLI help/schema facts: `features list --disable` reflects effective state; `--strict-config` unavailable on `features/mcp list`; `-c mcp_servers={}` has no effect (no deny-all); `Config.web_search` enum; Codex rollouts contain no nested `apply_patch` records; `CREATE_SUSPENDED` + job assignment behaviour | `docs/REMEDIATION_CHECKLIST_2.md`, `docs/evidence/capability_policy_real_cli.json` |
| **historical_report** (superseded; different code) | pre-remediation smoke/attack results, sandbox-lock diagnosis, first-remediation evidence | `docs/history/` |
| **assumption** | listed in §6 | – |

## 4. Test commands and results (actual)

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B tools\record_evidence.py fingerprint
.\.venv\Scripts\python.exe -B tools\record_evidence.py policy
.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label second_remediation_full
#  -> OK: 238 tests in 391.743s; failing=[]   (docs/evidence/tests_second_remediation_full.json)
.\.venv\Scripts\python.exe -B tools\verify_lock.py
#  -> environment matches requirements.lock.txt (30 packages)
.\.venv\Scripts\python.exe -B tools\v1_manifest.py compare docs\v1_baseline_before.json
```

* **238 tests, 0 failures, 0 skipped**, recorded against source hash `07106480…d373`; no
  subscription calls (scripted fake CLI as a real child process; real file locks, real
  process trees, real Job Objects, real git). By module: `test_review2` 65 (new: N1–N15,
  accounting lag, prompt builder), `test_remediation` 48, `test_service` 43,
  `test_apply_patch_attack` 35, `test_config_argv_auth` 18, `test_state` 18,
  `test_server_admin` 11. Expectations in the older modules changed only where behaviour
  changed intentionally (flag line prepended, P2 reconcile, durable admission, `findstr`
  stage, degraded removed, exact-patch oracle); the diff shows each change.
* **Two things the suite taught me that you should know:** (1) one test
  (`SandboxPreflight`) was *flaky* (1 run in 6) because job accounting lags a normal exit by
  ~4 ms — a real product defect (a spurious `reaped_descendants` warning), fixed with a grace
  window and pinned by tests, not hidden by a retry; (2) the first version of the N1 control
  test *skipped* because `;` in a git config value is a comment, so the helpers never fired —
  a skipped control proves nothing, so it now fails if the control does not fire and a
  mutation test requires it to fail when containment is removed.
* **Concurrency caveat:** an earlier full run (225 tests) had the one flaky failure above
  while other test processes were running; during the recorded run I made only documentation
  edits and started no other test or process-heavy work.
* **v1 integrity:** `v1_files`, `v1_listing`, `v1_state`, `v1_venv` unchanged. The manifest
  reports `codex_stat.config.toml` mtime changed (size identical, 3348 B); `auth.json`
  unchanged (mtime 2026-10-10). Attribution: the new mtime (12:57:31Z) is the same one-time
  event as in the previous round: `ChatGPT.exe` started at 12:57:23Z and rewrote the file
  ~8 s later; the model-free `codex` calls made in this pass started after 16:48Z and did not
  change it. Content equality cannot be proven (only size/mtime are recorded; credentials are
  never opened). I did not edit it.
* **State of the machine at record time:** ChatGPT desktop app and its `node_repl.exe`
  processes were running (`sandbox_provisioning_at_record_time: failed` in every evidence
  record). That is the known sandbox-refresh lock; it is irrelevant to the mocked tests and is
  the reason no live check was attempted.
* Tool limit: `record_evidence.py` records results; it does not prove completeness, and the
  evidence is implementer-executed.

## 5. Threat boundary and the precise read-only claim

Bounded claim (README §Threat boundary): the source pins `sandbox_mode="read-only"` and
`approval_policy="never"` on every fresh and resumed call and refuses to build an argv that
loosens either; unit tests assert that pinning and the failure handling. **Native shell /
`apply_patch` write rejection has not been live-verified for this source hash**; historical
rejection applies only to earlier candidates and the conditions they ran under.

Not claimed: that all capabilities are read-only; that no external side effects occur;
repository confidentiality (content is sent to OpenAI by design); that the bridge writes
nothing outside the repository (bridge state, Codex history/sessions and sandbox ACL setup are
written elsewhere); anything for other CLI versions or an unavailable sandbox; that
bridge-originated execution (host git, the `codex sandbox` preflight) is inside the Codex
sandbox.

| Component | Constrained by | Not constrained |
|---|---|---|
| shell / patch tools | read-only sandbox (OS) + core approval policy (argv-pinned) | live rejection for this hash not re-run |
| host-side git (fingerprint, probe selection) | `gitsafe` controls | `$GIT_DIR/info/attributes`; git bugs |
| preflight `codex sandbox` | model-free stages with the call's overrides; `findstr` with name as data | not the same code path as `codex exec` |
| MCP servers, plugins, apps, hooks, browser/computer use | feature gates + per-server disables (resolved, snapshot-scoped) | allow-listed servers (uncontained); no deny-all; race after the final recheck |
| delegation, image generation | feature gates (resolved) | runtime tool removal unverified |
| web | `web_search="disabled"` (requested) | no readout; `web__run` mapping unknown |
| view_image, skills, goals | – | uncontained (not repo-write routes) |
| bridge/Codex state, sandbox ACLs | v2 state outside repo, journal | written by design |
| processes started by the Windows sandbox service | operator reconcile after timeout/cancel | cannot be enumerated or confirmed terminated |

## 6. Remaining unverified (explicit)

1. Live tool inventory under the new policy (delegation/image/web actually absent?).
2. Whether `web_search="disabled"` removes `web__run`.
3. Live `--json` item shapes for failed commands (fixtures derive from rollout records).
4. The preflight `findstr` stage and suspended start against the real CLI and real launcher.
5. Attack tests, smoke test, and the MCP stdio/service path for **this** source hash.
6. That the real engine's access-denial message carries a code recognised by stage A.
7. Behaviour on the `unelevated` backend through `codex exec`.
8. Non-job-owned sandbox helper processes after timeout/cancel.
9. Any CLI version other than 0.162.1; non-Windows platforms.
10. Mutation sensitivity of tests other than N1's control.

## 7. Sanitized telemetry examples (fake CLI; fixture values)

Full set: `docs/telemetry_examples_remediation.jsonl` (15 records; checked for paths, prompt
text and the malformed identifier: none present). Content-free by construction; the set
covers the second-pass refusals (N3, N6, N7, N8, N14, N15, P2, P3). Selected, abbreviated:

```json
{"_example":"N7 pre-completion error event","outcome":"error","error_category":"sandbox_unavailable","error_code":"turn_sandbox_setup_failed","pointer_state":"unchanged","history":"turn_may_exist"}
{"_example":"P3 degraded mode removed","outcome":"error","error_category":"invalid_configuration","error_code":"bad_env","history":"no_turn_started","inference_may_have_started":false}
{"_example":"N6 pending_persistence blocks a silent replacement thread","outcome":"error","error_category":"session_in_doubt","error_code":"pending_persistence","session_state":"pending_reconcile","history":"no_turn_started"}
{"_example":"N3 ownership_unavailable","outcome":"error","error_category":"subprocess_failed","error_code":"ownership_unavailable","history":"no_turn_started","inference_may_have_started":false}
{"_example":"P2 timeout: job members confirmed, service children unobservable","outcome":"timeout","session_state":"in_doubt","process":{"ownership":"job_object","termination":"confirmed","active_after":0,"service_children_unobservable":true},"warnings":["service_children_unobservable"]}
{"_example":"N15 fingerprint unavailable","outcome":"success","warnings":["repo_fingerprint_unavailable:timeout"]}
{"_example":"N8 mcp_changed_before_exec","outcome":"error","error_category":"capability_audit_failed","error_code":"mcp_changed_before_exec","history":"no_turn_started"}
{"_example":"N14 bad_thread_id","outcome":"error","error_category":"output_incomplete","error_code":"bad_thread_id","pointer_state":"unchanged"}
```

## 8. Decisions (all pending; implementing a conservative candidate is not approval)

| # | Decision | Options | GPT's round-1 recommendation | State in this candidate |
|---|---|---|---|---|
| **D1** | Per-call disabling of MCP/plugins/hooks/apps/browser/computer use | (a) empty allow-list; (b) allow specific servers; (c) reject | retain (a); resolve the config race before relying on containment; exceptions need an operational review | (a) is the default; the race is **documented, not closed** |
| **D3** | Activation style | side-by-side then cut-over; direct; defer | **defer while High findings remain**; then side-by-side with edits paused and evidence bound to the corrected source | nothing registered |
| **D8-web** | `web_search` | keep disabled; enable under a written egress policy | keep disabled; verify the runtime mapping | disabled, no exception mechanism |
| **D8-delegation/image** | `multi_agent`, `image_generation` | keep disabled; re-enable selectively | keep disabled; need runtime evidence | disabled (resolved) |
| **P2** | sandbox-service commands unobservable | accept; reconcile after every timeout/cancel; investigate | operator reconciliation after every timeout/cancel + investigate | option (b) implemented as the conservative default; the human may relax it |
| **P3** | `degraded` mode | keep opt-in; remove; diagnostic surface | remove from the production success path | removed on the human's instruction |

## 9. The verification prompt (sent once, human-authorised, 2026-10-12T17:11Z) and its result

`docs/gpt_verification/verification_prompt.md` + `verification_manifest.json`
(about 565 KB, 48 entries (37 embedded in full, 1 as a diff against the version GPT reviewed, 10 hash-listed only); the manifest has exact sizes and per-file hashes). Built by `tools/make_verification_prompt.py`, which refuses unless every evidence
record is bound to the current source hash and the test run passed, and which imports nothing
able to start a process or open a connection. It frames each claim as a hypothesis to falsify,
embeds the code (including `redact.py`, `gitsafe.py`, `ids.py`, the fake CLI and the evidence
recorder that GPT said it lacked), lists declared limits, and specifies the output (verdict
table, new findings incl. regressions, test adequacy, evidence honesty, decisions, next checks,
missing material). The file is byte-identical to what the manifest hashes (`prompt_sha256`).

**Sending it consumes subscription allowance and is a separate step that needs the human's
explicit authorisation.** This package deliberately contains no sender. Round 1 used a
one-off script (not part of the package) that ran `codex exec` under the production argv,
capability policy and runner against a neutral disposable repository; the prompt is
self-contained, so the call needs no repository access. Expect roughly 140K input tokens
(round 1 was 81K and took about 10 minutes). Pasting the file into the ChatGPT app is the
alternative, but then none of the bridge's own controls apply to that call.

### Result of the send (one attempt, no retry)

Sent after re-verifying the prompt hash and that the evidence still matched the source; run
through the production argv/runner with an empty MCP allow-list against a neutral disposable
repository. `docs/gpt_verification/request_20261012T171145Z.json`: exit 0, turn completed,
thread `01a13119-58e8-7c31-a3bf-cf2920f90eac`, 144,687 input / 18,590 output tokens, **0
command/tool items, 0 infrastructure failures**, 681 s, job-owned and terminated cleanly.
GPT's review, verbatim: `docs/gpt_verification/gpt_verification_20261012T171145Z.md`
(advisory; not approval).

**Headline:** no Critical finding; **the candidate is not ready to be relied on**. Verdicts:
CLOSED — N13, N15, P3; NOT CLOSED — N4, N6, N9; PARTIALLY CLOSED / CONTRADICTED as stated —
N1, N2, N3, N5, N7, N8, N10, N11, N12, N14. Of F1–F8, F5 and F7 are NOT CLOSED; the rest
PARTIALLY CLOSED. New findings V1–V15 (7 High, 6 Medium, 2 Low):

| # | Sev | Finding (GPT) | Implementer's check |
|---|---|---|---|
| V1 | High | runner cleanup can itself fail: `Thread.start()` failure makes `finally` join unstarted threads before `owner.close()`; `ProcessOwnership()`/`Events()` built outside the `try` | read in code: real |
| V2 | High | stale recovery clears `completed_unbound` even when the replacement pending marker could not be written; a failed phase update lets a completed turn look abandoned | read in code: pending-marker result is ignored before the clear: real |
| V3 | High | hard bridge death bypasses P2: dead owner + job ownership ⇒ auto-recovery although service-spawned commands are unobservable | design gap, consistent with my own declared limit |
| V4 | High | `_telemetry_dir` falls back to an **unvalidated** default, so telemetry can be written inside a repository after `state_in_repo` rejected the call | read in code: real (narrow trigger: home inside the reviewed repo) |
| V5 | High | forged wrapper (`if (false) await tools.apply_patch(...)`, rejection text assembled at runtime) still passes the oracle | plausible from the textual checks; my wording "excludes forged output" overclaimed |
| V6 | High | host git is resolved as bare `git` (CreateProcess searches the current directory/PATH), so containment flags do not constrain executable selection | confirmed: bare `"git"` in `gitsafe` |
| V7 | High | `_gather_text` does not traverse `content`; a signature in the middle of an oversized line is lost; oversized prose can be flagged | plausible from the code |
| V8 | Med | `adopt`/`reset` do not clear a retained `persistence_failed` marker; `reconcile` clears it without a thread disposition | plausible |
| V9 | Med | `begin_maintenance` overwrites a stale flag; an unlink error in `end_maintenance` keeps the lock | read in code: real |
| V10 | Med | an allow-listed server first enabled at the pre-exec recheck is filtered out and never reported | plausible |
| V11 | Med | stage A accepts a **read** denial (`Error reading canary.txt: Access is denied`) as write-enforcement evidence; controls do not check the update result | plausible |
| V12 | Med | reader exceptions in `_run_capture` are not recorded; an incomplete HEAD read becomes `UNBORN` | plausible |
| V13 | Med | evidence is fingerprinted after the tests run (no before/after check); the prompt gate ignores `exit_code`/`skipped` | read in code: real |
| V14 | Low | only `banners` trigger the flag line; README says details precede the answer, the source puts them after it | **README is wrong** (I wrote it) |
| V15 | Low | `$` in the id/token regexes accepts a trailing newline; `sanitise` restores the original | **confirmed by execution** |

GPT also lists the material it still lacks (full `tests/test_remediation.py`, the production
diff, three helper tools, `live_smoke_test.py`, raw test output, the manifests) and
recommends: defer activation (D3) while the High findings remain; keep P2 conservative and
extend it to hard-owner death; keep P3 removed; keep web/delegation/image disabled; resolve
V1–V5 and V8 before the smoke/service-path checks, V1 and V5 before stage B, V11 before reading
stage A as enforcement evidence, and V13 before treating any new result as hash-bound.

**Nothing has been changed in response.** The source hash and all evidence above are still
those of the candidate GPT reviewed. Any remediation needs a new instruction and a full
re-record.

## 10. Proposed opt-in live checks and activation/rollback — **NOT EXECUTED**

Preconditions: ChatGPT/Codex apps closed, `Get-Process node_repl` empty, human authorisation
for allowance use. Each is bound to the hash it runs against.

1. `python tools\record_evidence.py fingerprint` (confirm the hash is unchanged since this package).
2. `bridge_admin.py status --sandbox` on a disposable repo — exercises the `findstr` stage.
3. `python tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance` — 2 turns:
   reads, continuity, telemetry, tool inventory for delegation/image/web (**new evidence for F2**).
4. `python tools\apply_patch_attack_test.py` (stage A), then `--live …` (stage B, 2 turns).
   Stage B covers production argv + runner, not the MCP/service path. The oracle is
   wrapper-attested.
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
removed). Registration and removal commands do not touch v1 state or Codex history; the
*evaluation calls* made while v2 is registered do create Codex threads and session files under
`~/.codex`, and v2 state under `%USERPROFILE%\.gpt-architect-bridge-v2`, which rollback does not
delete.

## 11. Files

`REVIEW_PACKAGE.md` (this), `README.md` (spec/threat boundary), `docs/REMEDIATION_CHECKLIST.md`,
`docs/REMEDIATION_CHECKLIST_2.md`, `docs/remediation2.diff`, `docs/remediation2_stat.txt`,
`docs/remediation.diff`, `docs/remediation_stat.txt`, `docs/evidence/*.json`,
`docs/gpt_review/` (GPT's round-1 review, verbatim), `docs/gpt_verification/` (prepared prompt),
`docs/telemetry_examples_remediation.jsonl`, `docs/history/` (superseded material),
`.baseline_pre_remediation/` and `.baseline_post_remediation1/` (diff bases).
