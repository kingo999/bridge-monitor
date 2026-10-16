# v2 fifth-remediation review package — for GPT (verification) and the human

Prepared 2026-10-13. Implementer: Claude Code. **v2 is not registered, not activated, not
committed or published. v1 is untouched and live.** This pass authorised remediation of GPT's
round-4 verification findings S1–S15 only; D1/D3/D8, activation and any v1 change remain with the
human.

* Source under review: `C:\AI\gpt-claude-mcp-bridge-v2` — combined SHA-256
  **`f68b192cba158e44b104cc823789e9f2a821f170b4c8b0417e36eacb1fb7467a`** (53 files,
  `docs/evidence/source_fingerprint.json`; scope defined in `tools/record_evidence.py::source_files`
  — `docs/` and this file are NOT hashed).
* State GPT verified in round 4 (diff base): `.baseline_post_remediation4/` (its `MANIFEST.json`
  is embedded; its `combined_sha256_all_files` `bffeffd9…` is a DIFFERENT scope from the evidence
  hash, `hash_scope` in the manifest says so; the evidence hash at that review was `2551d85f…7dba`).
  Earlier bases: `.baseline_post_remediation3/`, `…2/`, `…1/`, `.baseline_pre_remediation/`.
* **No live model call was made to produce the evidence in this package.** All results are
  mocked-CLI or model-free against the installed CLI. A call for THIS source is described in §10
  and has NOT been made.
* Results recorded for earlier candidates (smoke 16/16, attack 5/5 and 13/13) are
  **historical** (`docs/history/`) and are not evidence for this code.
* Who produced the evidence: the implementer (Claude Code) on the human's machine. The class is
  `implementer_executed`: a reviewer cannot confirm totals from the record alone, which is why the
  raw logs, the staged-copy binding and before/after fingerprints are retained (§5).

## 1. Round 4 in one paragraph (context)

GPT verified the fourth remediation (`docs/gpt_verification/gpt_verification_20261013T080140Z.md`,
embedded verbatim): no Critical finding; **defer activation (D3)**. CLOSED R6, R10, R11; NOT CLOSED
R1 and R13; the other eight R findings PARTIALLY CLOSED; the four cross-cutting design claims
(A: no dead-owner recovery / process fact durable first; B: independent facts through one
derivation; C: real targets and OS-derived tools; D: unreadable evidence never becomes a review)
were all graded CONTRADICTED. New findings S1–S15 (5 High, 9 Medium, 1 Low). Nothing was changed
until the human instructed "yes remediate S1-S15".

## 2. What changed (diff: `docs/remediation5.diff`, stat `docs/remediation5_stat.txt`,
source-only `docs/remediation5_src.diff` (the prompt embeds a computed `bridge/` diff); cumulative `docs/remediation.diff`)

| Area | Files | Finding |
|---|---|---|
| Disposition commands never settle a process fact; durable `thread_settled` | `bridge/admin.py`, `bridge/state.py` | S1, S2 |
| Primary uncertainty write checked before mirroring | `bridge/service.py` | S3 |
| Reader must reach EOF; sealed events; shared validity predicate | `bridge/runner.py` | S4 (+ S14) |
| Attributed operations | `tools/smoke_oracles.py` | S5 |
| Output-position scanning, full JSON escapes, bounded signature | `bridge/runner.py` | S6 |
| System tools fail closed | `bridge/execpath.py` | S7 |
| JavaScript lexical validity | `tools/apply_patch_attack_test.py` | S8 |
| Read-only stage, post-run staged fingerprint | `tools/record_evidence.py` | S9 |
| Prefix evidence chain | `tools/prefix_check.py`, `tools/make_verification_prompt.py` | S10 |
| One guarded no-execution cleanup | `bridge/service.py` | S11 |
| Warnings on error exits | `bridge/service.py` | S12 |
| Establishment evidence after one call | `bridge/state.py` | S13 |
| The sender is reviewed source | `tools/send_verification.py` (new) | S14 |
| Exact banner code; flag reporting on every admin path | `bridge/service.py`, `bridge/admin.py` | S15 |
| Review's other items | `bridge/codexcli.py` (readout completeness, allowed-server union), `tests/test_review2.py` (N1 requires all three helper kinds; trusted git), `tests/test_review4.py` | also |
| Tests | `tests/test_review5.py` (new, 50), updated expectations in 3 modules, `tests/helpers.py` | all |

Written plan and decisions: `docs/REMEDIATION_CHECKLIST_5.md` (written while implementing, and
says so).

## 3. Finding-by-finding disposition

Vocabulary: **Implemented + tested (mocked)** = the code does it and an automated test exercises
it; it is *not* "closed" until GPT or the human says so. **Documented limit** = not eliminated,
stated plainly.

| # | Finding | Disposition | What is true now | What is NOT closed |
|---|---|---|---|---|
| **S1** | reset/adopt deleted a raw `running` record | Implemented + tested | only a pure `persistence_failed` thread debt is removed; every other record is kept (and the command says so); settlement failure exits 1. My R1 test that REQUIRED the deletion was reversed | the operator now needs `reconcile` after reset/adopt of a raw stale record (deliberate) |
| **S2** | discarded disposition re-derived | Implemented + tested | `thread_settled` is written on the retained record and `thread_debt` honours it, including `pointer_bound` records | a different thread id is a different debt (by design) |
| **S3** | primary write ignored | Implemented + tested | 3 attempts; failure ⇒ `uncertainty_marker_unwritable`, raw record untouched, no pending write | crash points beyond those injected |
| **S4** | live reader not incomplete | Implemented + tested | alive-after-join / never-EOF ⇒ `reader_failed`; events sealed; `runner.turn_problems` | a reader that blocks forever still costs the 5 s join |
| **S5** | oracles forgeable via text | Implemented + tested (pure functions) | only attributed operations (native function call, or strict one-call pass-through wrapper) can satisfy a matcher; writes matched by verb and target; refusal wording must not come from the command | **real shapes of shell calls inside the `exec` wrapper have not been observed live**: real runs may be INCONCLUSIVE; harness not run live |
| **S6** | skipped request-named keys; `\n` escapes | Implemented + tested | request keys skipped only outside returned containers; all JSON string escapes resolved; window 4096; bounded `\s{0,1024}` | container names not in `_OUTPUT_KEYS`; >1024 whitespace characters between `CreateProcess` and `{` |
| **S7** | env fallback for system tools | Implemented + tested | `SystemDirectoryUnavailable`; callers treat the tool as unavailable | npm launcher's own JavaScript is not examined |
| **S8** | invalid JavaScript attested | Implemented + tested | reserved words rejected as identifiers; raw CR/LF excluded from quoted strings; template CRLF normalised | other JS lexical corners (still INCONCLUSIVE by omission, not modelled) |
| **S9** | stage writable, not re-fingerprinted | Implemented + tested (functional) | stage read-only; fingerprinted again before deletion; a test that rewrites a staged module invalidates the record | read-only is an attribute, not a sandbox; environment not staged |
| **S10** | prefix evidence unchecked | Implemented + tested | overlay verified against the manifest, expected count, loader errors, failure kinds; builder re-verifies the chain | — |
| **S11** | inconsistent no-execution cleanup | Implemented + tested | one routine; fallback persistence checked and reported | — |
| **S12** | error exits hide warnings | Implemented + tested | recorded banners are appended to categorised errors | non-`BridgeError` exceptions are wrapped as before (no block) |
| **S13** | one-call loss looked like first init | Implemented + tested | prev written with the first write; the journal is a second witness (`pointer_not_saved` excluded) | the journal can be lost too |
| **S14** | sender bypassed validity checks | Implemented + tested | `tools/send_verification.py` with the package's predicate and an acknowledgement flag | not run live in this form |
| **S15** | prefix banner suppression; flag note | Implemented + tested | exact code registered; note printed on every admin path | — |

### Evidence/test wording items from the review

* The N1 control now requires ALL THREE helper kinds to fire with plain git (a probe showed
  `core.fsmonitor` does fire here with git 2.51.2); a machine where it does not makes the control
  fail loudly instead of weakening silently.
* The R4 tautology (`assertIsNone(x and None)`) was removed; every launch of `git` in `tools/` and
  `tests/` uses the absolute trusted executable (the N1 control uses it *without* containment flags).
* The gate counts per-test result lines (one header and one `ok` per test), not only the footer.
* `cmd_fingerprint`'s record states that its `environment` block was produced by executing
  model-free CLI commands (`environment_evidence_class`).
* `Audit.allowed_servers` now accumulates the discovery list as well as the final and later readouts.
* Configuration readouts are refused when truncated or when a drain thread failed (`Simple.complete`).

### Found by the implementer while doing this (not in GPT's list)

* My own first journal witness included `pointer_not_saved`, an event that records that the
  registry write FAILED; it made a state directory that never had a registry look "lost" (8
  of the 15 failures in the first full run, caught by pre-existing tests). Removed, with a note in code.
* Files I patched with scripts silently changed from CRLF to LF; restored where the snapshot was
  CRLF (cosmetic, but it had made the raw diff unreadable).
* One S10 fixture wrote text-mode files and so hashed different bytes on Windows (newline
  translation); fixed to bytes.
* **Unexplained configuration change (disclosure):** `codex_stat.config.toml` differs from the
  original baseline by size AND mtime again: size 3348 → 3402, mtime 2026-10-13 08:00:01Z — about
  100 s BEFORE the round-4 send started (08:01:40Z) and in a window where I had executed only
  model-free `codex` subcommands in disposable directories. I did not edit it and never open it;
  attribution (the ChatGPT desktop app, or a Codex CLI write such as a project-trust entry) is NOT
  established. `auth.json` is unchanged (mtime 2026-10-10 12:23Z). The human may want to inspect it.
* Carried disclosures: the round-2 prompt embedded the account name in two fake path literals in
  `tests/test_review2.py`.

## 4. Evidence classes

| Class | Items | Bound to |
|---|---|---|
| **implementer_executed** | 380-test suite from a read-only staged copy (before/after/staged/staged-after fingerprints equal, exit 0, 0 skipped, raw log `docs/evidence/tests_fifth_remediation_full.log`, hash-listed in the prompt); `prefix_check` of the 50 new tests against the pre-fix source; effective capability policy from the real CLI; v1/auth integrity output and lock verification; model-free CLI observations (`kind: model_free_cli_observation`); generated diffs | source hash `f68b192c…467a`, CLI 0.162.1, sandbox backend recorded in each record |
| **source_observation** | structure of one historical live-attack rollout (read, not executed) | source hash, CLI 0.162.1 |
| **historical_report** | earlier smoke/attack results, earlier evidence records (`docs/history/evidence_remediation{1,2,3,4}`) | `docs/history/` |
| **assumption** | §6 | – |

## 5. Test commands and results (actual)

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B tools\record_evidence.py fingerprint
.\.venv\Scripts\python.exe -B tools\record_evidence.py policy
.\.venv\Scripts\python.exe -B tools\record_evidence.py integrity
.\.venv\Scripts\python.exe -B tools\record_evidence.py cli --rollout <historical rollout>
.\.venv\Scripts\python.exe -B tools\prefix_check.py --snapshot .baseline_post_remediation4 --module tests.test_review5 --guards <7 declared>
.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label fifth_remediation_full
#  -> OK: 380 tests in 552.2s; failing=[]; problems=[]
```

* **380 tests, 0 failures, 0 skipped**, recorded against `f68b192c…467a`. By module:
  `test_review2` 65, `test_review3` 51, `test_review5` 50 (new), `test_remediation` 48,
  `test_service` 43, `test_review4` 40, `test_apply_patch_attack` 36, `test_config_argv_auth` 18,
  `test_state` 18, `test_server_admin` 11.
* **Do the new tests detect the defects?** Run against the pre-fix source (the frozen
  `.baseline_post_remediation4` `bridge/` and `tools/` plus the current harness), with the overlay
  verified against the snapshot manifest: **43 of the 50 new tests fail or error; 7 pass by
  declared design** (regression guards: a pure thread debt is still removed by reset; genuine
  attributed writes are still recognised; the request itself is not evidence; the whitespace scan
  stays fast; an escaped `\r` is still a CR; an empty directory is not an established registry; no
  bare `git` launch). **Of the 43: 36 are behavioural assertion failures and 7 fail only because
  the new API does not exist yet** (S9/S10/readout-completeness tests) — weaker evidence, kept
  distinct in `docs/evidence/prefix_check.json` (`failure_kind_by_test`). Mutation sensitivity of
  *older* tests was not re-checked.
* **Process notes:** the first full run during development had 15 failures: 8 exposed my own
  journal-witness bug (above), fixed in code; 7 were tests or fixtures that encoded behaviour I
  deliberately replaced (reset/adopt deleting a raw record, text-matching oracles, a simplistic
  prefix fixture, a footer-only log fixture) - rewritten, not loosened. The recorded run had no
  concurrent work.
* **v1 integrity:** `v1_files`, `v1_listing`, `v1_state`, `v1_venv` unchanged; lock verified (30
  packages). `codex_stat.config.toml` changed again (see §3, disclosure); `auth.json` unchanged.
* **Machine state:** the ChatGPT desktop app (and `node_repl.exe`) may be running at record time
  (`sandbox_provisioning_at_record_time` is recorded in each record: `failed` for this run): the
  known sandbox-refresh lock, irrelevant to the mocked tests and the reason no live attack/smoke
  check was attempted.

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
| which program runs (git, codex, node, taskkill, tasklist) | `execpath` resolver (absolute PATH entries outside cwd/project/repo; file targets and shim artifacts validated; System32 via `GetSystemDirectoryW`, no fallback) | the user's own PATH; hard links; DLL search order; check-then-use; what the npm launcher's JavaScript executes next |
| what host git does | `gitsafe` flags + scrubbed env + minimum version | `$GIT_DIR/info/attributes`; git bugs |
| shell / patch tools | read-only sandbox + approval policy (argv-pinned) | live rejection for this hash not re-run |
| preflight `codex sandbox` | model-free stages; `findstr` with name as data | not the same path as `codex exec` |
| MCP/plugins/hooks/apps/browser/computer use | feature gates + per-server disables (snapshot-scoped); post-audit unexpected servers reported | allow-listed servers; no deny-all; race after the last recheck |
| delegation, image generation | feature gates (resolved) | runtime removal unverified |
| web | `web_search="disabled"` (requested) | no readout |
| processes started by the Windows sandbox service | operator reconcile after timeout/cancel/bridge death | cannot be enumerated or confirmed gone |
| test provenance | read-only staged copy + staged before/after + live before/after fingerprints + retained log + per-test accounting | environment (PATH, `%TEMP%`, venv, CLI) is not staged; read-only is an attribute |

## 7. Remaining unverified (explicit)

1. Live tool inventory under the policy (delegation/image/web actually absent?).
2. Whether `web_search="disabled"` removes `web__run`.
3. Live `--json` item shapes for failed commands.
4. The `findstr` stage, the suspended start and the target-validating resolver against the
   real launcher on a clean machine, including a real symlink/junction (the test simulates the target).
5. Attack tests, smoke test (including the attributing oracles) and the MCP stdio/service path for
   **this** source hash; in particular what shapes a real model's recorded shell calls take.
6. That the real engine's denial message prints a path form the exact comparison recognises.
7. Behaviour on the `unelevated` backend through `codex exec`.
8. Service-spawned sandbox processes after timeout/cancel/bridge death.
9. Any CLI version other than 0.162.1; non-Windows platforms.
10. Mutation sensitivity of tests older than this round (other than the N1 control).
11. Whether `--ignore-user-config` would change the MCP set (not observable model-free).
12. The cause of the `config.toml` size/mtime change (§3).

## 8. Sanitized telemetry examples (fake CLI; fixture values)

`docs/telemetry_examples_remediation.jsonl` — 17 records regenerated in round 4 with the fake CLI
(no model call); this round's behaviours are covered by tests, not by new example records.
Content-free by construction.

## 9. Decisions (all pending; implementing a conservative candidate is not approval)

| # | Decision | GPT's recommendation (rounds 2–4) | State in this candidate |
|---|---|---|---|
| **D1** | MCP/plugins/hooks/apps/browser/computer use; `--ignore-user-config` | retain the empty allow-list; do not adopt the flag silently; treat the final-load race as a human risk decision | empty default; resolver validates targets and fails closed; the race is documented, not closed; the flag is recorded, not adopted |
| **D3** | Activation style | **defer while High findings remain**; then side-by-side with edits paused | nothing registered |
| **D8-web** | `web_search` | keep disabled | disabled, no exception mechanism |
| **D8-delegation/image** | gates | keep disabled pending runtime evidence | disabled (resolved) |
| **P2** | service-spawned commands | conservative reconciliation that survives raw reset/adopt and unsuccessful state writes | implemented; reset/adopt can no longer drop the process fact; the primary write is checked |
| **P3** | `degraded` mode | retain removal | removed; warnings rendered before the answer and on errors |

## 10. The verification prompt

`docs/gpt_verification/verification_prompt.md` + `verification_manifest.json` (exact sizes, per-file
hashes, `prompt_sha256`, per-file `scrubbed_local_identifiers`). Built by
`tools/make_verification_prompt.py`, which refuses unless every embedded evidence record
(fingerprint, policy, integrity, CLI, prefix check, tests) is bound to the current hash, the test
record was executed from a staged copy that was re-fingerprinted after the run, its retained log
accounts for every test, and the prefix chain (raw log, outcomes, expected count, overlay hashes,
snapshot manifest) checks out; it imports nothing able to start a process or open a connection.
Files byte-identical to the version GPT verified last round are hash-listed, not repeated, except
the three test bodies the round-4 review asked for in full; the passing run's raw log is
hash-listed (the pre-fix raw output IS embedded). The round-4 prompt is archived in
`docs/history/gpt_verification_round4/`.

Sending consumes subscription allowance and needs the human's explicit authorisation each time.
The sender is `tools/send_verification.py` (reviewed source: acknowledgement flag, trusted git, one
attempt, `runner.turn_problems` validity predicate).

### Result of the fifth-round send (one attempt, no retry; sent 2026-10-13T09:44Z, human-authorised)

`docs/gpt_verification/request_20261013T094415Z.json`: exit 0, turn completed, stdout read to EOF,
thread `01a134a6-0642-70f2-9c47-aa2671336f13`, 218,529 input / 23,368 output tokens (13,403
reasoning), **0 command/tool items, 0 infrastructure failures**, 885 s, `valid_review` true.
Review (verbatim, advisory): `docs/gpt_verification/gpt_verification_20261013T094415Z.md`. Prompt
sent: 848,200 bytes, `prompt_sha256` `ba0a15c5…`, source `f68b192c…`. This package text was edited
afterwards only to record this result.

**Headline:** no Critical finding; **defer activation (D3)**. Of S1-S15: CLOSED S7, S9, S11, S15;
NOT CLOSED S5; PARTIALLY CLOSED the other ten. Of R1-R13: CLOSED R3, R6, R7, R8, R10; NOT CLOSED
R13; CANNOT VERIFY R11; PARTIALLY CLOSED six. Cross-cutting A and D CONTRADICTED, B CONTRADICTED as
unconditional, C confirmed within its declared boundary. Several older findings (R11, V12, V15,
N11, N14) are CANNOT VERIFY **because this prompt omitted files that were unchanged since the
version GPT verified** (hash-listed only): the omission policy cost verifiability and should not
be repeated for the files named in its Missing-material list. New findings T1-T12 (2 High, 8 Medium,
2 Low); the implementer's reading of the code agrees with T1, T6, T7 and T12.

| # | Sev | Finding (GPT) |
|---|---|---|
| T1 | High | an abandoned reader callback (`on_thread`/`on_started`) writes the stale in-memory `marker` whole after the call returned, erasing `thread_settled` or a later call's record; `Events.seal` is a Boolean checked at method entry |
| T2 | High | `_writes` matches write verbs inside quoted text / unreachable branches, `_refused` is not attributed to that write (`Write-Output "Set-Content X"; Get-Content y` + a denied read = PASS); unknown tool names and malformed arrays accepted |
| T3 | Med | `persistence_failed` is taken as "pure thread debt" from one field; a conflicting record is deleted by reset/adopt |
| T4 | Med | ordinary-turn writes: all uncertainty/completion writes can fail while the pending write succeeds; observer and final marker results unchecked |
| T5 | Med | `OversizeScan` carry window (4096 B) cannot hold 1024 six-byte ` ` escapes + `CreateProcess` across a chunk boundary |
| T6 | Med | the generic-exception branch of `execute` wraps without `_attach_warnings` |
| T7 | Med | `turn_problems` / `review_is_valid` accept a turn with no observed thread id; the tests endorse it |
| T8 | Med | the builder trusts overlay lists (module missing is accepted), does not rehash snapshot bytes, and `log_problems` accepts repeated lines |
| T9 | Med | grammar regexes use Python `\s` (U+0085 etc. is not JavaScript whitespace) |
| T10 | Med | the first-write backup/journal witnesses are best-effort; `register_new` journals nothing; `restore-prev` advice is impossible when identity is missing |
| T11 | Low | "byte-identical" omission compares normalised text, not raw bytes |
| T12 | Low | staged mutation is labelled `FAILED`, not `INVALID` |

Also: `apply_patch_attack_test.py::stage_b` still decides "completed" without `runner.turn_problems`;
the retained `features list` text is a clipped tail; the empty-table probe shows only that THIS
override did not disable the servers. GPT's cheapest-checks and missing-material lists are in the
review.

**Nothing has been changed in response.** The source hash and all evidence are those GPT reviewed.

## 11. Proposed opt-in live checks and activation/rollback — **NOT EXECUTED**

Preconditions: ChatGPT/Codex apps closed, `Get-Process node_repl` empty, human authorisation for
allowance use. Each is bound to the hash it runs against.

1. `python tools\record_evidence.py fingerprint` (confirm the hash is unchanged).
2. `bridge_admin.py status --sandbox` on a disposable repo — exercises the `findstr` stage.
3. `python tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance` — 2 turns
   (the oracles attribute operations from the recorded rollout; this harness has never been run in
   this form, and unobserved call shapes are INCONCLUSIVE).
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
`docs/remediation5.diff` + `_stat.txt` + `remediation5_src.diff`, `docs/remediation*.diff`,
`docs/evidence/*` (incl. `.log`, `prefix_check.*`), `docs/gpt_review/` (round 1),
`docs/gpt_verification/` (round-4 review, request, manifest; the prepared prompt),
`docs/telemetry_examples_remediation.jsonl`, `docs/history/`, `.baseline_*` (diff bases).
