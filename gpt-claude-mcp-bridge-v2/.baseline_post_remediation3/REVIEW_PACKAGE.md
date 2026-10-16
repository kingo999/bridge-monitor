# v2 third-remediation review package — for GPT (verification) and the human

Prepared 2026-10-13. Implementer: Claude Code. **v2 is not registered, not activated, not
committed or published. v1 is untouched and live.** This pass authorised remediation of GPT's
verification findings V1–V15 only; D1/D3/D8, activation and any v1 change remain with the human.

* Source under review: `C:\AI\gpt-claude-mcp-bridge-v2` — combined SHA-256
  **`c58537c24582c347cb4323d4c713d998dff8fafad50ff5ed4968a38dfc65ee49`** (48 files,
  `docs/evidence/source_fingerprint.json`).
* State GPT verified in round 2 (diff base): `.baseline_post_remediation2/` (manifest in it;
  evidence hash at that review `07106480…d373`). Earlier bases: `.baseline_post_remediation1/`
  (round 1), `.baseline_pre_remediation/` (original candidate).
* **No live model call was made to produce the evidence in this package.** All results are
  mocked-CLI or model-free against the installed CLI. One human-authorised model call was made
  in round 2 (GPT's verification of the previous source); a call for THIS source is described
  in §9.
* Results recorded for earlier candidates (smoke 16/16, attack 5/5 and 13/13) are
  **historical** (`docs/history/`) and are not evidence for this code.
* Who produced the evidence: the implementer (Claude Code) on the human's machine. The class
  is `implementer_executed`: a reviewer cannot confirm totals from the record alone, which is
  why the raw log and before/after fingerprints are retained (§3).

## 1. Round 2 in one paragraph (context)

GPT verified the second remediation (`docs/gpt_verification/gpt_verification_20261012T171145Z.md`,
embedded verbatim in the prompt): no Critical finding; CLOSED N13, N15, P3; NOT CLOSED N4, N6,
N9; the rest PARTIALLY CLOSED; new findings V1–V15 (7 High). It recommended deferring
activation. Nothing was changed until the human instructed "remediate V1-V15".

## 2. What changed (diff: `docs/remediation3.diff`, stat `docs/remediation3_stat.txt`,
source-only `docs/remediation3_src.diff` (the prompt embeds a computed `bridge/` diff); cumulative `docs/remediation.diff`)

| Area | Files | Finding |
|---|---|---|
| Runner cleanup | `bridge/runner.py` | V1 |
| Stale recovery / state machine | `bridge/service.py`, `bridge/admin.py`, `bridge/state.py` | V2, V3, V8, V9 |
| Telemetry boundary + formats + typed fields | `bridge/service.py`, `bridge/telemetry.py`, `bridge/ids.py`, `bridge/config.py` | V4, V15 |
| Executable resolution | `bridge/execpath.py` (new), `bridge/gitsafe.py`, `bridge/codexcli.py` | V6 |
| Infrastructure evidence | `bridge/runner.py` (`_gather_text`, `OversizeScan`) | V7 |
| Capability recheck | `bridge/codexcli.py`, `bridge/service.py` | V10 |
| Attack oracle | `tools/apply_patch_attack_test.py` | V5, V11 |
| Fingerprint | `bridge/fingerprint.py` | V12 |
| Evidence binding + privacy | `tools/record_evidence.py`, `tools/privacy.py` (new), `tools/make_verification_prompt.py` | V13 |
| Warning rendering | `bridge/service.py`, `README.md` | V14 |
| Tests | `tests/test_review3.py` (new, 51), updated expectations in 5 modules | all |

Written plan and decisions: `docs/REMEDIATION_CHECKLIST_3.md` (written while implementing, and
says so).

## 3. Finding-by-finding disposition

Vocabulary: **Implemented + tested (mocked)** = the code does it and an automated test
exercises it; it is *not* "closed" until GPT or the human says so. **Documented limit** = not
eliminated, stated plainly.

| # | Finding | Disposition | What is true now | What is NOT closed |
|---|---|---|---|---|
| **V1** | Runner cleanup can fail | Implemented + tested (real child, real job) | everything after `Popen` is in the cleanup scope; readers start first, writer last; only started threads are joined; `owner.close()` in an outermost `finally`; job terminated on every non-clean exit even if the root already exited; original categorised error kept | the real launcher/`codex` is not exercised by these tests (fake CLI and `python -c` children) |
| **V2** | Recovery deletes the only record | Implemented + tested | replacement durable state is written (and checked) before an in-flight record is removed; a running-phase dead-owner marker becomes `uncertain` (+ pending marker for a fresh observed thread); a failed write keeps blocking and later retries | none known beyond the crash points the tests inject |
| **V3** | Hard owner death bypasses P2 | Implemented + tested | no stale record is ever auto-recovered; the operator's `reconcile` is required | cost: an operator step after any bridge crash (deliberate; GPT's P2 recommendation) |
| **V4** | Telemetry can write into a repo | Implemented + tested | every candidate, including the fallback, is validated; none safe ⇒ record skipped with a content-free stderr line | — |
| **V5** | Forged wrappers pass | Implemented + tested | strict pass-through grammar; the patch is decoded from the call's argument; everything else INCONCLUSIVE (incl. GPT's exact `if (false)` forgery) | **still wrapper-attested** (no nested records exist); an unusual genuine wrapper is INCONCLUSIVE, not guessed; soundness rests on my reading of JS statement semantics — a prime review target |
| **V6** | Executable selection unconstrained | Implemented + tested | `bridge/execpath.py` resolves from absolute PATH entries outside cwd/`CLAUDE_PROJECT_DIR`/the repo; used for git, codex, node, taskkill, tasklist; all `GIT_*` env dropped case-insensitively. **Wider than asked**: the same hole existed for `codex` and `node`, not only `git` | trusts the user's own PATH outside the project; the `git` run *inside* the sandbox preflight is resolved by the sandbox; DLL search order of a resolved executable is not controlled |
| **V7** | Infra evidence incomplete | Implemented + tested | every output container is traversed (only input-ish keys skipped); oversized lines are streamed through the whole scan; prose item types excluded by parsing the type from the head | conservative false positives for unknown-type oversized records; live `--json` shapes not re-captured |
| **V8** | Recovery conflates process and thread | Implemented + tested | `reconcile` refuses an unsaved-thread record and converts an unbound thread into a pending marker; `adopt`/`reset` settle only the thread debt and keep independent process uncertainty; the service no longer overwrites `uncertain` | — |
| **V9** | Stale maintenance overwritten / lock leak | Implemented + tested | stale flag ⇒ `maintenance_stale` until `end-maintenance --yes`; lock released on every path; probe failure cleans up | — |
| **V10** | Late allow-listed server unreported | Implemented + tested | complete snapshot; late allowed servers become `uncontained` + banner; the last read follows the durable marker and a refusal clears it | **residual race documented, not closed** (no deny-all: `docs/evidence/cli_observations.json` records the probe) |
| **V11** | Read denial counted as write evidence | Implemented + tested (oracle only) | write-operation failure naming the attacked path required (stage A and B); controls verify update/delete effects | **no live run**; if the real engine's text lacks a recognised denial code, stage A is INCONCLUSIVE |
| **V12** | Reader failure / incomplete HEAD | Implemented + tested | reader exceptions recorded; `UNBORN` only when verified | — |
| **V13** | Evidence bound to post-run tree | Implemented + tested | fingerprint before AND after, rejection of change/nonzero exit/skips, raw log retained with hash; gate checks all of it; `integrity`/`cli` evidence retained | still implementer-executed |
| **V14** | Warning rendering ≠ docs | Implemented + tested | `ctx.warn(code, banner)`; generic fallback line; details **before** the answer; README corrected | — |
| **V15** | `$` accepted a trailing newline | Implemented + tested | `fullmatch` everywhere; typed telemetry fields checked against their own enum/format | — |

### Found by the implementer while doing this (not in GPT's list)

* `codex`, `node`, `taskkill`, `tasklist` had the same current-directory resolution hole as `git` (V6, widened).
* A stale `persistence_failed` record never retried its conversion to a pending marker (found by a test I wrote).
* The "which marker is reported first" order hid process uncertainty behind a thread debt.
* `rev-parse --verify HEAD` does not check the object exists (`HEAD^{commit}` now).
* **Privacy:** my first `cli` evidence stored raw `mcp list --json` output (local runtime paths,
  pipe names, environment). It was caught before anything was embedded or sent and replaced by
  normalised `(name, enabled)` lists; the prompt builder now also scrubs the profile path and
  account name from everything it embeds. **Disclosure:** the round-2 prompt (already sent)
  embedded `tests/test_review2.py`, which contains the account name in two fake path
  literals.
* My own README said warning details precede the answer when the code put them after (V14 was right).

## 4. Evidence classes

| Class | Items | Bound to |
|---|---|---|
| **implementer_executed** | 290-test suite (before/after fingerprints equal, exit 0, 0 skipped, raw log `docs/evidence/tests_third_remediation_full.log`); effective capability policy from the real CLI; v1/auth integrity output and lock verification (`docs/evidence/integrity.json`); generated diffs | source hash `c58537c2…ee49`, CLI 0.162.1, sandbox backend recorded in each record |
| **source_observation** | model-free CLI help/schema text, the MCP deny-all probe (normalised effective view identical with `-c mcp_servers={}`, servers still enabled), and the structure of a historical live-attack rollout (`exec` calls only, 0 records named `apply_patch`) — `docs/evidence/cli_observations.json` | source hash, CLI 0.162.1 |
| **historical_report** | earlier smoke/attack results, earlier evidence records | `docs/history/` |
| **assumption** | §6 | – |

## 5. Test commands and results (actual)

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B tools\record_evidence.py fingerprint
.\.venv\Scripts\python.exe -B tools\record_evidence.py policy
.\.venv\Scripts\python.exe -B tools\record_evidence.py integrity
.\.venv\Scripts\python.exe -B tools\record_evidence.py cli --rollout <historical rollout>
.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label third_remediation_full
#  -> OK: 290 tests in 423.896s; failing=[]; problems=[]
```

* **290 tests, 0 failures, 0 skipped**, recorded against `c58537c2…ee49`; the tree was
  byte-identical before and after the run. By module: `test_review3` 51 (new), `test_review2`
  65, `test_remediation` 48, `test_service` 43, `test_apply_patch_attack` 36,
  `test_config_argv_auth` 18, `test_state` 18, `test_server_admin` 11. Older expectations changed
  only where behaviour intentionally changed (auto-recovery removed, disabled preflight now
  bannered, textual oracle replaced by the grammar, evidence gate fields).
* **Do the new tests detect the defects?** Run against the pre-fix source (the frozen
  `.baseline_post_remediation2` `bridge/` and `tools/`): **48 of the 51 new tests fail or
  error; 3 pass by design** — they are regression guards that normal behaviour still works
  (a valid telemetry override is used; an invalid one falls back only to a validated default; the
  words in a command are not infrastructure evidence). Mutation sensitivity of *older* tests was
  not re-checked (the N1 control retains its own mutation test).
* **Process notes:** one full run during development showed 8 failures, all tests that encoded
  behaviour I deliberately removed (auto-recovery, an undisplayed disabled preflight, textual
  forgery checks); they were rewritten, not loosened. The recorded run had no concurrent work.
* **v1 integrity:** `v1_files`, `v1_listing`, `v1_state`, `v1_venv` unchanged; lock verified.
  `codex_stat.config.toml` mtime changed again (size 3348 identical); `auth.json` unchanged
  (mtime 2026-10-10). Attribution: the new mtime (2026-10-13 06:20:24Z) follows a ChatGPT app
  restart at 06:20:14Z, 10 s earlier, and precedes my first evidence command (06:24:15Z): the
  same pattern as the two previous events. Content equality cannot be proven (only size/mtime
  are recorded; credentials are never opened). I did not edit it.
* **Machine state:** the ChatGPT desktop app (and `node_repl.exe`) was running at record time
  (`sandbox_provisioning_at_record_time: failed` in the records): the known sandbox-refresh
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
| which program runs (git, codex, node, taskkill, tasklist) | `execpath` resolver (absolute PATH entries outside cwd/project/repo; System32 absolute) | the user's own PATH |
| what host git does | `gitsafe` flags + scrubbed env + minimum version | `$GIT_DIR/info/attributes`; git bugs |
| shell / patch tools | read-only sandbox + approval policy (argv-pinned) | live rejection for this hash not re-run |
| preflight `codex sandbox` | model-free stages; `findstr` with name as data | not the same path as `codex exec` |
| MCP/plugins/hooks/apps/browser/computer use | feature gates + per-server disables (snapshot-scoped) | allow-listed servers; no deny-all; race after the last recheck |
| delegation, image generation | feature gates (resolved) | runtime removal unverified |
| web | `web_search="disabled"` (requested) | no readout |
| processes started by the Windows sandbox service | operator reconcile after timeout/cancel/bridge death | cannot be enumerated or confirmed gone |

## 7. Remaining unverified (explicit)

1. Live tool inventory under the policy (delegation/image/web actually absent?).
2. Whether `web_search="disabled"` removes `web__run`.
3. Live `--json` item shapes for failed commands.
4. The `findstr` stage, the suspended start and the trusted-executable resolution against the
   real launcher on a clean machine.
5. Attack tests, smoke test and the MCP stdio/service path for **this** source hash.
6. That the real engine's denial message carries a code the oracle recognises.
7. Behaviour on the `unelevated` backend through `codex exec`.
8. Service-spawned sandbox processes after timeout/cancel/bridge death.
9. Any CLI version other than 0.162.1; non-Windows platforms.
10. Mutation sensitivity of tests older than this round (other than the N1 control).

## 8. Sanitized telemetry examples (fake CLI; fixture values)

`docs/telemetry_examples_remediation.jsonl` — 17 records including this round's V3 (stale
record after bridge death ⇒ `uncertain_termination`, no turn) and V10 (allow-listed server
enabled only at the last recheck ⇒ reported as uncontained). Content-free by construction.

## 9. Decisions (all pending; implementing a conservative candidate is not approval)

| # | Decision | GPT's round-2 recommendation | State in this candidate |
|---|---|---|---|
| **D1** | MCP/plugins/hooks/apps/browser/computer use | retain the empty allow-list; resolve V10 and trusted executable selection first | empty default; V10 and V6 implemented; the race is documented, not closed |
| **D3** | Activation style | **defer while High findings remain**; then side-by-side with edits paused | nothing registered |
| **D8-web** | `web_search` | keep disabled | disabled, no exception mechanism |
| **D8-delegation/image** | gates | keep disabled pending runtime evidence | disabled (resolved) |
| **P2** | service-spawned commands | conservative reconciliation, extended to hard-owner death | implemented for timeout, cancel AND bridge death |
| **P3** | `degraded` mode | retain removal; fix warning visibility | removed; V14 implemented |

## 10. The verification prompt

`docs/gpt_verification/verification_prompt.md` + `verification_manifest.json` (exact sizes,
per-file hashes, `prompt_sha256`, per-file `scrubbed_local_identifiers`). Built by
`tools/make_verification_prompt.py`, which refuses unless every evidence record is bound to the
current hash, was not changed during its run, and records a passing, skip-free run with an intact
retained log; it imports nothing able to start a process or open a connection. It frames each
claim as a hypothesis to falsify, embeds the code and the material GPT said it lacked in round 2
(full `tests/test_remediation.py`, the source diff, the helper tools, the v1 fixtures, the raw
test log, the integrity/CLI evidence, the previous manifest), lists declared limits and specifies
the output. Round-2's prompt is archived in `docs/history/gpt_verification_round2/`.

Sending consumes subscription allowance and needs the human's explicit authorisation each time.
The sender is a one-off script outside the package (production argv/runner, neutral disposable
repository, empty MCP allow-list, no retry); it re-verifies the prompt hash and the evidence
binding before sending.

### Result of the third-round send (one attempt, no retry; sent 2026-10-13T06:44Z, human-authorised)

`docs/gpt_verification/request_20261013T064429Z.json`: exit 0, turn completed, thread
`01a13401-7067-70f2-86f0-cba7b154cde7`, 184,975 input / 18,758 output tokens, **0 command/tool
items, 0 infrastructure failures**, 757 s. Review (verbatim, advisory):
`docs/gpt_verification/gpt_verification_20261013T064429Z.md`. The prompt sent was the one
described above (723,272 bytes, `prompt_sha256` `5d7efb70…`, source `c58537c2…`); this package
text was edited afterwards only to record this result.

**Headline:** no Critical finding; **not ready to rely on; defer activation (D3)**. Of V1-V15:
CLOSED V4; NOT CLOSED V5, V6; PARTIALLY CLOSED the other twelve. Earlier findings: CLOSED N2, N13,
N14, N15, P3; NOT CLOSED N1, N6, N9; the rest PARTIALLY CLOSED; F5 and F7 NOT CLOSED.
New findings R1-R13 (4 High, 6 Medium, 3 Low). Reproduced by the implementer against the
current code: R5 (shadowed result name and a binding declared after use are both accepted;
trailing junk after the patch is not caught), R6 (substring basename), R11 (list in an enum
field raises and drops the record); R1/R7/R8 match the code as read; R3 (link target) not
reproduced here (symlink creation needs privileges) but the code path is as described.

| # | Sev | Finding (GPT) |
|---|---|---|
| R1 | High | `reconcile` can clear a raw stale record (fresh thread known, no `unbound_thread`/`persistence_failed`) without preserving the thread debt; an interrupted fresh turn that reported a thread keeps only `thread_id` |
| R2 | High | a failed `uncertain` write leaves `running`; a later successful phase update makes the call look persistence-only; also a crash window between pending write and uncertain update |
| R3 | High | the resolver validates the PATH directory, not the file's real target (link into the repo; npm shim to an adjacent node/`codex.js`); `live_smoke_test.py` and `tests/helpers.py` still use bare `git` |
| R4 | High | stdout-reader exceptions are swallowed; traversal depth cap and the skip list drop output-bearing keys (`files`, `path`, `status`); `_`-escaped signatures in oversized lines; item-type regex can match a nested `item` |
| R5 | High | wrapper grammar: result-name shadowing, bindings declared after use, trailing junk after the patch, backslash-n normalisation |
| R6 | Med | "names the target" is a basename substring |
| R7 | Med | `begin_maintenance`'s new existence check is outside the lock-release scope; callers ignore a failed flag removal |
| R8 | Med | a marker-unlink failure in `abort_before_exec` masks the refusal and leaves false inference/history accounting |
| R9 | Med | the prompt gate does not validate `integrity.json`/`cli_observations.json`; summaries are not reconciled with the log; endpoint equality is not continuous identity |
| R10 | Med | two allocations after `Popen` precede the protected `try`; cleanup polling can mask the original error |
| R11 | Low | non-string enum values raise and drop the whole telemetry record |
| R12 | Low | the generic warning fallback trusts a prefix exemption |
| R13 | Med | smoke-test oracles treat narration as execution evidence |

Also: this package referred to live checks as §10 in README/prompt but they are §11 (the same
section-reference slip GPT flagged in round 1); `source_observation` is defined as "no
execution" yet includes executed model-free commands; the snapshot hash scope vs the evidence
hash scope is not explained; the deny-all probe shows only that THIS override did not disable
servers (`--ignore-user-config` exists and was not examined). GPT's cheapest-checks list and
missing-material list are in the review.

**Nothing has been changed in response.** The source hash and all evidence are those GPT reviewed.

## 11. Proposed opt-in live checks and activation/rollback — **NOT EXECUTED**

Preconditions: ChatGPT/Codex apps closed, `Get-Process node_repl` empty, human authorisation for
allowance use. Each is bound to the hash it runs against.

1. `python tools\record_evidence.py fingerprint` (confirm the hash is unchanged).
2. `bridge_admin.py status --sandbox` on a disposable repo — exercises the `findstr` stage.
3. `python tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance` — 2 turns.
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
`docs/remediation3.diff` + `_stat.txt` + `remediation3_src.diff`, `docs/remediation*.diff`,
`docs/evidence/*` (incl. `.log`), `docs/gpt_review/` (round 1), `docs/gpt_verification/`
(round-2 review, request, manifest; the prepared prompt), `docs/telemetry_examples_remediation.jsonl`,
`docs/history/`, `.baseline_*` (diff bases).
