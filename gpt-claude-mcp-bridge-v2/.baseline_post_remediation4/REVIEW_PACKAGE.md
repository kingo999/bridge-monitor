# v2 fourth-remediation review package — for GPT (verification) and the human

Prepared 2026-10-13. Implementer: Claude Code. **v2 is not registered, not activated, not
committed or published. v1 is untouched and live.** This pass authorised remediation of GPT's
round-3 verification findings R1–R13 only; D1/D3/D8, activation and any v1 change remain with the
human.

* Source under review: `C:\AI\gpt-claude-mcp-bridge-v2` — combined SHA-256
  **`2551d85fa0e89333ffb12903133ac1091928dc1924b70504f98b8e0569987dba`** (51 files,
  `docs/evidence/source_fingerprint.json`; scope defined in `tools/record_evidence.py::source_files`
  — `docs/` and this file are NOT hashed).
* State GPT verified in round 3 (diff base): `.baseline_post_remediation3/` (its `MANIFEST.json`
  is embedded; its `combined_sha256_all_files` `dee9a3ae…` is a DIFFERENT scope from the evidence
  hash, `hash_scope` in the manifest says so; the evidence hash at that review was `c58537c2…ee49`).
  Earlier bases: `.baseline_post_remediation2/`, `.baseline_post_remediation1/`,
  `.baseline_pre_remediation/`.
* **No live model call was made to produce the evidence in this package.** All results are
  mocked-CLI or model-free against the installed CLI. A call for THIS source is described in §10
  and has NOT been made.
* Results recorded for earlier candidates (smoke 16/16, attack 5/5 and 13/13) are
  **historical** (`docs/history/`) and are not evidence for this code.
* Who produced the evidence: the implementer (Claude Code) on the human's machine. The class is
  `implementer_executed`: a reviewer cannot confirm totals from the record alone, which is why
  the raw logs, the staged-copy binding and before/after fingerprints are retained (§5).

## 1. Round 3 in one paragraph (context)

GPT verified the third remediation (`docs/gpt_verification/gpt_verification_20261013T064429Z.md`,
embedded verbatim): no Critical finding; "not ready to rely on; defer activation (D3)". Of
V1–V15: CLOSED V4; NOT CLOSED V5, V6; the other twelve PARTIALLY CLOSED. New findings R1–R13
(4 High, 6 Medium, 3 Low), plus corrections to evidence wording and the retained N10/V12 items.
Nothing was changed until the human instructed "remediate R1-R13".

## 2. What changed (diff: `docs/remediation4.diff`, stat `docs/remediation4_stat.txt`,
source-only `docs/remediation4_src.diff` (the prompt embeds a computed `bridge/` diff); cumulative `docs/remediation.diff`)

| Area | Files | Finding |
|---|---|---|
| Thread debt derivation, reconcile/adopt/reset | `bridge/state.py` (`thread_debt`), `bridge/admin.py`, `bridge/service.py` | R1 |
| Durable process fact, whole-record writes | `bridge/service.py` (`_check_session_health`, `_locked_call`) | R2 |
| Execution targets | `bridge/execpath.py`, `bridge/codexcli.py`, `bridge/gitsafe.py`, `tools/live_smoke_test.py`, `tests/helpers.py` | R3 |
| Evidence completeness | `bridge/runner.py`, `bridge/service.py` | R4 |
| Wrapper grammar, exact denial target | `tools/apply_patch_attack_test.py` | R5, R6 |
| Maintenance flag | `bridge/state.py`, `bridge/admin.py` | R7 |
| Refusal cleanup | `bridge/service.py` (`abort_before_exec`) | R8 |
| Evidence gate, staged execution, prefix check | `tools/record_evidence.py`, `tools/make_verification_prompt.py`, `tools/prefix_check.py` (new) | R9 |
| Cleanup gaps | `bridge/runner.py` | R10 |
| Typed telemetry | `bridge/telemetry.py` | R11 |
| Rendered codes | `bridge/service.py`, `bridge/codexcli.py` | R12 |
| Smoke oracles | `tools/smoke_oracles.py` (new), `tools/live_smoke_test.py` | R13 |
| Retained N10 / V12 | `bridge/state.py`, `bridge/fingerprint.py` | N10, V12 |
| Tests | `tests/test_review4.py` (new, 40), updated expectations in 5 modules | all |

Written plan and decisions: `docs/REMEDIATION_CHECKLIST_4.md` (written while implementing, and
says so).

## 3. Finding-by-finding disposition

Vocabulary: **Implemented + tested (mocked)** = the code does it and an automated test exercises
it; it is *not* "closed" until GPT or the human says so. **Documented limit** = not eliminated,
stated plainly.

| # | Finding | Disposition | What is true now | What is NOT closed |
|---|---|---|---|---|
| **R1** | Raw stale record cleared without its thread debt | Implemented + tested | one derivation (`state.thread_debt`) for the service and every admin command, including on raw stale records and fresh-turn thread announcements; `reconcile` writes the debt to a pending marker first and refuses on failure | a debt now also arises after a timeout on a FRESH call that announced a thread — one extra operator step (`adopt`/`reset`), deliberate |
| **R2** | Failed `uncertain` write masked by a phase update | Implemented + tested | dead owner ⇒ `uncertain` in the primary record first (any phase), pending marker second; whole-record writes; failed write retried and reported (`uncertainty_marker_unwritable`) | crash points beyond those the tests inject |
| **R3** | Resolver validates directories, not targets | Implemented + tested (link target simulated) | file real target and shim-derived `node.exe`/`codex.js` validated (`cli_target_untrusted`); system tools from `GetSystemDirectoryW`; no bare `git` in tools/tests | symlinks need privilege: the test patches the real-path lookup, a real link/junction was not created; hard links, DLL search order, check-then-use |
| **R4** | Evidence silently incomplete | Implemented + tested | reader failure and traversal limits ⇒ `turn_evidence_incomplete` (answer withheld); output-bearing keys scanned; escapes resolved in oversized lines; item type read only from the canonical prefix | encodings other than JSON `\uXXXX`; live `--json` shapes for failed commands not re-captured; false positives are accepted |
| **R5** | Grammar mis-models JS | Implemented + tested | program-order evaluation; shadow/reuse rejected; complete argument must equal the expected patch (CRLF/trailing newline only) | still wrapper-attested; hoisting/getters/ASI/template forms are INCONCLUSIVE by omission, not modelled — a prime review target |
| **R6** | "Names the target" = basename substring | Implemented + tested (oracle only) | `Failed to <verb> <PATH>` parsed; full normalised path compared under the recorded base; unparseable ⇒ INCONCLUSIVE | the engine's real path forms (`\\?\`, 8.3, casing) are unverified live |
| **R7** | Maintenance existence check outside lock-release scope | Implemented + tested | every post-lock operation guarded; commands exit 1 when the flag cannot be removed | — |
| **R8** | Unlink failure masks the refusal | Implemented + tested | facts corrected first; guarded clear; durable `refused_before_exec` marker cleared by the next health check | — |
| **R9** | Gate and provenance gaps | Implemented + tested | tests run from a staged copy; every embedded record bound to the hash; summary reconciled with the log; `prefix_check` retained | staged copy isolates the executed source, not the environment (PATH, `%TEMP%`, venv, CLI); endpoint equality is not continuous identity |
| **R10** | Allocation / cleanup gaps | Implemented + tested | all bookkeeping before `Popen`; `poll()` guarded | — |
| **R11** | Non-string enum value drops the record | Implemented + tested | `isinstance(value, str)` before membership | — |
| **R12** | Prefix exemption hides warnings | Implemented + tested | exact codes in `ctx.bannered`; everything else rendered generically; post-audit unexpected servers named (`mcp_unexpected_seen`) | — |
| **R13** | Smoke oracles trust narration | Implemented + tested (pure functions) | control/attempt/refusal from the recorded rollout; inventory is a labelled self-report | **the harness was not run live**; only the pure oracle functions are tested |
| N10 (retained) | Both identity files lost | Implemented + tested | `registry.prev.json` proves loss (`registry_lost`); first initialisation still allowed | — |
| V12 (retained) | UNBORN inferred | Implemented + tested | UNBORN only when `show-ref --verify` exits 1 | — |

### Evidence wording corrected (GPT's "also")

* Executed model-free CLI commands are `implementer_executed` (`kind: model_free_cli_observation`);
  `source_observation` now means read from code/docs only.
* Hash scopes: the snapshot manifest's combined hash covers all files; the evidence hash covers the
  source set above. Both are defined in the manifest and README.
* The deny-all probe shows only that THIS override (`-c mcp_servers={}`) did not disable the listed
  servers. `--ignore-user-config` was examined model-free: `mcp list` rejects it, so its effect on
  the MCP set cannot be observed that way; it is NOT adopted (D1, human decision).
* README/prompt section references: live checks are §11.

### Found by the implementer while doing this (not in GPT's list)

* `adopt` first passed the bound thread as the "already bound" argument and so settled nothing.
* `quarantine` returned 0 when it could not remove the maintenance flag.
* One R7 test patched `Path.exists` for the second registry as well and proved nothing; one R4
  reader test died before `turn.completed` and so did not discriminate; an escaped-signature test
  also contained other signatures and passed for the wrong reason. All three were rewritten.
* **Disclosures (carried):** the round-2 prompt (already sent) embedded the account name in two
  fake path literals in `tests/test_review2.py`; `config.toml` mtime changes in earlier rounds were
  attributed to ChatGPT app restarts (credentials never opened).

## 4. Evidence classes

| Class | Items | Bound to |
|---|---|---|
| **implementer_executed** | 330-test suite from a staged copy (before/after/staged fingerprints equal, exit 0, 0 skipped, raw log `docs/evidence/tests_fourth_remediation_full.log`); `prefix_check` of the 40 new tests against the pre-fix source; effective capability policy from the real CLI; v1/auth integrity output and lock verification; model-free CLI observations (`kind: model_free_cli_observation`); generated diffs | source hash `2551d85f…7dba`, CLI 0.162.1, sandbox backend recorded in each record |
| **source_observation** | structure of one historical live-attack rollout (read, not executed) | source hash, CLI 0.162.1 |
| **historical_report** | earlier smoke/attack results, earlier evidence records (`docs/history/evidence_remediation{1,2,3}`) | `docs/history/` |
| **assumption** | §6 | – |

## 5. Test commands and results (actual)

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B tools\record_evidence.py fingerprint
.\.venv\Scripts\python.exe -B tools\record_evidence.py policy
.\.venv\Scripts\python.exe -B tools\record_evidence.py integrity
.\.venv\Scripts\python.exe -B tools\record_evidence.py cli --rollout <historical rollout>
.\.venv\Scripts\python.exe -B tools\prefix_check.py --snapshot .baseline_post_remediation3 --module tests.test_review4 --guards <2 declared>
.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label fourth_remediation_full
#  -> OK: 330 tests in 479.6s; failing=[]; problems=[]
```

* **330 tests, 0 failures, 0 skipped**, recorded against `2551d85f…7dba`, executed from a staged
  copy whose fingerprint equals the live tree's before and after. By module: `test_review2` 65,
  `test_review3` 51, `test_remediation` 48, `test_service` 43, `test_review4` 40 (new),
  `test_apply_patch_attack` 36, `test_config_argv_auth` 18, `test_state` 18, `test_server_admin` 11.
* **Do the new tests detect the defects?** Run against the pre-fix source (the frozen
  `.baseline_post_remediation3` `bridge/` and `tools/` plus the current harness): **38 of the 40
  new tests fail or error; 2 pass by declared design** (regression guards: a genuine first
  initialisation is still allowed; `reset` clears a thread debt but keeps independent process
  uncertainty). **Qualification:** of the 38, 25 are assertion failures and 13 are *errors*, several
  of which are missing-API errors (`smoke_oracles`, `patch_matches`, etc.): they show the API is
  new, which is weaker evidence than a behavioural assertion failing on old code. The per-test
  outcomes are in `docs/evidence/prefix_check.json`. Mutation sensitivity of *older* tests was
  not re-checked.
* **Process notes:** an earlier full run during development had 11 failures, all tests that encoded
  behaviour I deliberately replaced (completed-unbound ⇒ pending, the `_BANNERED_ELSEWHERE`
  prefix exemption, OS-denial without a repo root); they were rewritten, not loosened. A later
  full run failed once because the R9 test helper had not been updated for the new
  `prefix_check.json` binding check; fixed, and the recorded run is after that fix. The recorded
  run had no concurrent work.
* **v1 integrity:** `v1_files`, `v1_listing`, `v1_state`, `v1_venv` unchanged; lock verified (30
  packages). `codex_stat.config.toml` differs from the original baseline by the same mtime as in
  round 3 (2026-10-13 06:20:24Z, size 3348); it did **not** change again during this round.
  `auth.json` unchanged (mtime 2026-10-10). Content equality cannot be proven (only size/mtime are
  recorded; credentials are never opened). I did not edit it.
* **Machine state:** the ChatGPT desktop app (and `node_repl.exe`) may be running at record time
  (`sandbox_provisioning_at_record_time` is recorded in each record): the known sandbox-refresh
  lock, irrelevant to the mocked tests and the reason no live attack/smoke check was attempted.

## 6. Threat boundary and the precise read-only claim

Bounded claim (README §Threat boundary): the source pins `sandbox_mode="read-only"` and
`approval_policy="never"` on every fresh and resumed call and refuses to build an argv that
loosens either; unit tests assert that pinning and the failure handling. **Native shell /
`apply_patch` write rejection has not been live-verified for this source hash.** Historical
rejection applies only to earlier candidates and the conditions they ran under.

Not claimed: that all capabilities are read-only; that no external side effects occur;
repository confidentiality (content is sent to OpenAI by design); that the bridge writes nothing
outside the repository; anything for other CLI versions or an unavailable sandbox; that
bridge-originated execution (host git, the `codex sandbox` preflight) is inside the Codex
sandbox; or that a compromised PATH entry outside the project is contained.

| Component | Constrained by | Not constrained |
|---|---|---|
| which program runs (git, codex, node, taskkill, tasklist) | `execpath` resolver (absolute PATH entries outside cwd/project/repo; file targets and shim artifacts validated; System32 via `GetSystemDirectoryW`) | the user's own PATH; hard links; DLL search order; check-then-use |
| what host git does | `gitsafe` flags + scrubbed env + minimum version | `$GIT_DIR/info/attributes`; git bugs |
| shell / patch tools | read-only sandbox + approval policy (argv-pinned) | live rejection for this hash not re-run |
| preflight `codex sandbox` | model-free stages; `findstr` with name as data | not the same path as `codex exec` |
| MCP/plugins/hooks/apps/browser/computer use | feature gates + per-server disables (snapshot-scoped); post-audit unexpected servers reported | allow-listed servers; no deny-all; race after the last recheck |
| delegation, image generation | feature gates (resolved) | runtime removal unverified |
| web | `web_search="disabled"` (requested) | no readout |
| processes started by the Windows sandbox service | operator reconcile after timeout/cancel/bridge death | cannot be enumerated or confirmed gone |
| test provenance | staged copy + before/after fingerprints + retained log | environment (PATH, `%TEMP%`, venv, CLI) is not staged |

## 7. Remaining unverified (explicit)

1. Live tool inventory under the policy (delegation/image/web actually absent?).
2. Whether `web_search="disabled"` removes `web__run`.
3. Live `--json` item shapes for failed commands.
4. The `findstr` stage, the suspended start and the target-validating resolver against the real
   launcher on a clean machine, including a real symlink/junction (the test simulates the target).
5. Attack tests, smoke test (including the rewired oracles) and the MCP stdio/service path for
   **this** source hash.
6. That the real engine's denial message prints a path form the exact comparison recognises.
7. Behaviour on the `unelevated` backend through `codex exec`.
8. Service-spawned sandbox processes after timeout/cancel/bridge death.
9. Any CLI version other than 0.162.1; non-Windows platforms.
10. Mutation sensitivity of tests older than this round (other than the N1 control).
11. Whether `--ignore-user-config` would change the MCP set (not observable model-free).

## 8. Sanitized telemetry examples (fake CLI; fixture values)

`docs/telemetry_examples_remediation.jsonl` — 17 records regenerated from the current code with the
fake CLI (no model call). The round-4 behaviours (R1/R2/R4/R12) are covered by the tests, not by
new example records. Content-free by construction.

## 9. Decisions (all pending; implementing a conservative candidate is not approval)

| # | Decision | GPT's recommendation (rounds 2–3) | State in this candidate |
|---|---|---|---|
| **D1** | MCP/plugins/hooks/apps/browser/computer use; `--ignore-user-config` | retain the empty allow-list; resolve trusted-executable selection | empty default; resolver validates targets; the race is documented, not closed; the flag is recorded, not adopted |
| **D3** | Activation style | **defer while High findings remain**; then side-by-side with edits paused | nothing registered |
| **D8-web** | `web_search` | keep disabled | disabled, no exception mechanism |
| **D8-delegation/image** | gates | keep disabled pending runtime evidence | disabled (resolved) |
| **P2** | service-spawned commands | conservative reconciliation, extended to hard-owner death | implemented for timeout, cancel AND bridge death; now independent of the phase reached |
| **P3** | `degraded` mode | retain removal; fix warning visibility | removed; warnings rendered before the answer |

## 10. The verification prompt

`docs/gpt_verification/verification_prompt.md` + `verification_manifest.json` (exact sizes, per-file
hashes, `prompt_sha256`, per-file `scrubbed_local_identifiers`). Built by
`tools/make_verification_prompt.py`, which refuses unless every embedded evidence record
(fingerprint, policy, integrity, CLI, prefix check, tests) is bound to the current hash, the test
record was executed from a staged copy, and its retained log reports the passing, skip-free run
the summary claims; it imports nothing able to start a process or open a connection. To keep the prompt within a size GPT has already handled
(round 3: 723 KB), the passing run's raw log is hash-listed in the manifest, not embedded; the
pre-fix raw output (`prefix_check.log`) IS embedded. It frames each
claim as a hypothesis to falsify, embeds the code, the newest review/request, the prior manifest and
the sender's source, lists declared limits and specifies the output. Round-3's prompt is archived
in `docs/history/gpt_verification_round3/`.

Sending consumes subscription allowance and needs the human's explicit authorisation each time.
The sender is a one-off script outside the package (production argv/runner, neutral disposable
repository, empty MCP allow-list, no retry); it re-verifies the prompt hash and the evidence
binding before sending. Its source is embedded for review
(`docs/gpt_verification/send_verification_source.py.txt`; not executable as stored). The copy
actually run differed from the embedded text in one respect: its scaffolding `git` calls used
`gitsafe.trusted_git` and `safe_git_env` instead of a bare `git` (R3).

### Result of the fourth-round send (one attempt, no retry; sent 2026-10-13T08:01Z, human-authorised)

`docs/gpt_verification/request_20261013T080140Z.json`: exit 0, turn completed, thread
`01a13448-18f9-7822-9341-e425b37136a5`, 231,410 input / 9,029 output tokens, **0 command/tool
items, 0 infrastructure failures**, 705 s. Review (verbatim, advisory):
`docs/gpt_verification/gpt_verification_20261013T080140Z.md`. Prompt sent: 868,098 bytes,
`prompt_sha256` `f963dbd2…`, source `2551d85f…`. This package text was edited afterwards only to
record this result.

**Headline:** no Critical finding; **defer activation (D3)**. R-verdicts: CLOSED R6, R10, R11;
NOT CLOSED R1, R13; PARTIALLY CLOSED R2, R3, R4, R5, R7, R8, R9, R12. All four cross-cutting
design claims (A–D) were graded CONTRADICTED. New findings S1–S15 (5 High, 9 Medium, 1 Low).
The implementer read `admin.py::_settle_unbound_thread` and confirms S1 from source: only the
literal state `uncertain` counts as process uncertainty, so reset/adopt delete a raw `running`
record, and my own `R1_..._raw_stale_record_too` test asserts that behaviour.

| # | Sev | Finding (GPT) |
|---|---|---|
| S1 | High | reset/adopt delete a raw `running` stale record (process uncertainty lost); a test requires it |
| S2 | High | reset clears `unbound_thread` but `thread_id` re-derives the debt; no durable "discarded" disposition |
| S3 | High | `_check_session_health` ignores `update_marker`'s Boolean; pending written though the primary did not persist |
| S4 | High | a stdout reader still alive after the bounded join is not marked incomplete |
| S5 | High | smoke oracles infer execution from call text; `text("git rev-parse HEAD <sha>")` / printed refusals can satisfy them |
| S6 | Med | nested returned `input`/`arguments` skipped globally; `\\n` escapes in oversized lines not decoded |
| S7 | Med | `system_tool` falls back to inherited `SystemRoot`/`windir` when the OS query fails |
| S8 | Med | wrapper grammar accepts JS keywords as identifiers and raw CR inside string literals |
| S9 | Med | staged copy is writable and not fingerprinted after the run |
| S10 | Med | prefix evidence: raw-log hash, overlay contents and loader-error-only runs not validated |
| S11 | Med | `refused_before_exec` write result ignored; spawn/ownership refusal branch clears before correcting facts |
| S12 | Med | error exits omit accumulated capability warnings |
| S13 | Med | registry + instance loss after ONE call (no `registry.prev.json`) still looks like first init |
| S14 | Med | the sender's `valid` predicate does not check reader failure/incomplete evidence/post-completion errors |
| S15 | Low | persistence-banner rendering still registers every `state_not_saved:` prefix as bannered; admin exceptions skip flag reporting |

Also: `Audit.note_allowed_enabled` does not accumulate a server seen only in the initial discovery
list; `codexcli.run_simple` silently tolerates reader failure/truncation; `cmd_fingerprint` stores
executed-CLI metadata inside a `source_observation` record; the N1 control does not exercise
fsmonitor; one R4 assertion (`assertIsNone(expression and None)`) is a tautology. GPT's
cheapest-checks and missing-material lists are in the review.

**Nothing has been changed in response.** The source hash and all evidence are those GPT reviewed.

## 11. Proposed opt-in live checks and activation/rollback — **NOT EXECUTED**

Preconditions: ChatGPT/Codex apps closed, `Get-Process node_repl` empty, human authorisation for
allowance use. Each is bound to the hash it runs against.

1. `python tools\record_evidence.py fingerprint` (confirm the hash is unchanged).
2. `bridge_admin.py status --sandbox` on a disposable repo — exercises the `findstr` stage.
3. `python tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance` — 2 turns
   (the oracles now read the recorded rollout; this harness has never been run in this form).
4. `python tools\apply_patch_attack_test.py` (stage A), then `--live …` (stage B, 2 turns).
   Stage B covers production argv + runner, not the MCP/service path; the oracle is
   wrapper-attested and strict.
5. A service-path live check (not yet scripted): one `consult_architect` through
   `architect_bridge.py` over stdio against a disposable repo.

Activation (side-by-side) — human only:
```powershell
cd C:\dev\your-repo
claude mcp add --scope local --transport stdio gpt-architect-v2 -- `
  "C:\AI\gpt-claude-mcp-bridge-v2\.venv\Scripts\python.exe" "C:\AI\gpt-claude-mcp-bridge-v2\architect_bridge.py"
```
Identify the server by name in each request; pause implementation edits during reviews.
Cut-over: remove `gpt-architect`, add v2 under that name. **Rollback:**
`claude mcp remove gpt-architect-v2 --scope local`. Registration and removal commands do not
touch v1 state or Codex history; the *evaluation calls* made while v2 is registered do create
Codex threads and session files under `~/.codex`, and v2 state under
`%USERPROFILE%\.gpt-architect-bridge-v2`, which rollback does not delete.

## 12. Files

`REVIEW_PACKAGE.md` (this), `README.md`, `docs/REMEDIATION_CHECKLIST*.md`,
`docs/remediation4.diff` + `_stat.txt` + `remediation4_src.diff`, `docs/remediation*.diff`,
`docs/evidence/*` (incl. `.log`, `prefix_check.*`), `docs/gpt_review/` (round 1),
`docs/gpt_verification/` (round-3 review, request, manifest; the prepared prompt; sender source),
`docs/telemetry_examples_remediation.jsonl`, `docs/history/`, `.baseline_*` (diff bases).
