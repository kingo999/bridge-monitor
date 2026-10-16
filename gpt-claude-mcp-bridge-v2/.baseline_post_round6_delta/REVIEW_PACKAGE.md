# v2 sixth-remediation review package — for GPT (verification) and the human

Prepared 2026-10-13. Implementer: Claude Code. **v2 is not registered, not activated, not
committed or published. v1 is untouched and live.** This pass authorised remediation of GPT's
round-5 verification findings T1–T12 only; D1/D3/D8, activation and any v1 change remain with the
human.

* Source under review: `C:\AI\gpt-claude-mcp-bridge-v2` — combined SHA-256
  **`05f6bd6c7307bea02e457415b525a8722148d72eae0fce4b8d829f3091c83438`** (55 files,
  `docs/evidence/source_fingerprint.json`; scope defined in `tools/record_evidence.py::source_files`
  — `docs/` and this file are NOT hashed).
* State GPT verified in round 5 (diff base): `.baseline_post_remediation5/` (its `MANIFEST.json` is
  embedded; the evidence hash at that review was `f68b192c…467a`; its `combined_sha256_all_files`
  is a DIFFERENT scope, `hash_scope` in the manifest says so). Earlier bases:
  `.baseline_post_remediation4/` … `.baseline_pre_remediation/`.
* **No live model call was made to produce the evidence in this package.** All results are
  mocked-CLI or model-free against the installed CLI. A call for THIS source is described in §10
  and has NOT been made.
* Results recorded for earlier candidates (smoke 16/16, attack 5/5 and 13/13) are
  **historical** (`docs/history/`) and are not evidence for this code.
* Who produced the evidence: the implementer (Claude Code) on the human's machine. The class is
  `implementer_executed`: a reviewer cannot confirm totals from the record alone, which is why the
  raw logs, the staged-copy binding and before/after fingerprints are retained (§5).

## 1. Round 5 in one paragraph (context)

GPT verified the fifth remediation (`docs/gpt_verification/gpt_verification_20261013T094415Z.md`,
embedded verbatim): no Critical finding; **defer activation (D3)**. Of S1–S15: CLOSED S7, S9, S11,
S15; NOT CLOSED S5; ten PARTIALLY CLOSED. R13 NOT CLOSED; cross-cutting claims A, B, D
CONTRADICTED. New findings T1–T12 (2 High: an abandoned reader callback writing stale state, and
smoke-oracle substring matching of shell text). Five older findings were CANNOT VERIFY because the
round-5 prompt had hash-listed unchanged files. Nothing was changed until the human instructed
"yes remediate T1-T12".

## 2. What changed (diff: `docs/remediation6.diff`, stat `docs/remediation6_stat.txt`,
source-only `docs/remediation6_src.diff` (the prompt embeds a computed `bridge/` diff); cumulative `docs/remediation.diff`)

| Area | Files | Finding |
|---|---|---|
| Observer fence + compare-and-merge, serialised marker writes, frozen events | `bridge/service.py`, `bridge/state.py`, `bridge/runner.py` | T1 |
| Shell grammar, known tools, specific denial wording | `tools/smoke_oracles.py` | T2 |
| Validated purity of `persistence_failed` | `bridge/state.py`, `bridge/admin.py`, `bridge/service.py` | T3 |
| Durable-first ordering on the ordinary path | `bridge/service.py` | T4 |
| Carry window holds the encoded signature | `bridge/runner.py` | T5 |
| Warnings on generic exceptions | `bridge/service.py` | T6 |
| Observed thread required | `bridge/runner.py`, `tools/apply_patch_attack_test.py` | T7 |
| Reconstructed prefix chain | `tools/prefix_logic.py` (new), `tools/prefix_check.py`, `tools/make_verification_prompt.py` | T8 |
| JavaScript whitespace | `tools/apply_patch_attack_test.py` | T9 |
| Mandatory first-write witness, recovery advice | `bridge/state.py` | T10 |
| Raw-byte omission | `tools/make_verification_prompt.py` | T11 |
| Outcome label | `tools/record_evidence.py` | T12 |
| Tests | `tests/test_review6.py` (new, 41), updated expectations in `test_review5`, helpers | all |

Written plan and decisions: `docs/REMEDIATION_CHECKLIST_6.md` (written while implementing, and
says so).

## 3. Finding-by-finding disposition

Vocabulary: **Implemented + tested (mocked)** = the code does it and an automated test exercises
it; it is *not* "closed" until GPT or the human says so. **Documented limit** = not eliminated,
stated plainly.

| # | Finding | Disposition | What is true now | What is NOT closed |
|---|---|---|---|---|
| **T1** | abandoned reader callback overwrites state | Implemented + tested (real threads) | observers merge only their own fields (`update_marker_if`: only into a record that still carries this call's `call_id`, never recreating one) and only while the call's fence is open; the fence is closed before any recovery write; every marker write/clear is serialised by `markers.lock`; `Events` mutators share one lock and `finish()` freezes the object after the join, the observer being called outside the lock | a legitimate late event after the join is dropped by design (the evidence is already incomplete); lock timeouts (5 s) turn a write into a reported failure |
| **T2** | substring matching of shell text | Implemented + tested (pure functions) | ONE simple command or nothing (separators, pipelines, sub-expressions, blocks, variables, comments, line breaks rejected; quoted strings are data); write = verb with the target as operand, or echo-like redirect; known tool names only; reserved result names and comma-less arrays rejected; denial wording must be specific and not supplied by the command | real shapes still unobserved: real runs may be INCONCLUSIVE; harness not run live |
| **T3** | purity inferred from one field | Implemented + tested | `persistence_failed` + a process-uncertainty reason is a process fact: reset/adopt keep it, reconcile treats it as such, the service converts it | records with other conflicting combinations are not enumerated |
| **T4** | ordinary-path ordering | Implemented + tested | the pending mirror requires a persisted uncertainty; completion/final/observer write failures are reported; banner says the pending marker was NOT written | crash points beyond those injected |
| **T5** | window too small | Implemented + tested | window 8192 ≥ the longest encoded signature; split-in-escape cases tested | whitespace runs > 1024; arbitrary `\uXXXX` (wording narrowed) |
| **T6** | generic exceptions drop warnings | Implemented + tested | the wrapped error carries the banners, `__cause__` preserved | — |
| **T7** | no thread id still valid | Implemented + tested | `turn_problems` needs a well-formed observed id and, on resume, the requested one; sender and Stage B use it | — |
| **T8** | prefix chain trusted | Implemented + tested | AST identities, complete overlay, snapshot bytes rehashed, kinds re-derived from the log, per-test lines unique and paired | the log itself is implementer-produced |
| **T9** | `\s` accepts non-JS whitespace | Implemented + tested | only space/tab/CR/LF between tokens; non-ASCII whitespace INCONCLUSIVE | other JS lexical corners (not modelled, INCONCLUSIVE by omission) |
| **T10** | witnesses best-effort | Implemented + tested | first-write backup mandatory and BEFORE the commit; `registry_established` event (also via `register_new`); advice no longer names an impossible command | a lost journal AND a failed backup are still not both covered (the first write is refused if the backup fails) |
| **T11** | normalised-text omission | Implemented + tested | raw bytes decide | — |
| **T12** | wrong label | Implemented + tested | provenance break ⇒ `INVALID`, failing tests ⇒ `FAILED` | — |

### Found by the implementer while doing this (not in GPT's list)

* My first design for T1 held the marker lock while an observer performed I/O, which would make
  every other writer wait on a stalled callback; the final design takes the lock only around the
  read-compare-write and keeps the callback itself outside every lock.
* `"not-a-thread"` is a VALID thread id under the format rule (letters, hyphens, ≥ 8): one of my
  new tests used it as an invalid example and was wrong, not the code.
* My first denial regex did not match PowerShell's `Access to the path is denied.` without a path.
* Round 5 omitted too much (§1): the omission policy is now an explicit allow-list
  (`ALWAYS_FULL`) naming every file the review asked for.
* Carried disclosure: the round-2 prompt embedded the account name in two fake path literals in
  `tests/test_review2.py`. `~/.codex/config.toml` is unchanged since round 5's record (size 3402,
  mtime 2026-10-13 08:00:01Z); the cause of that earlier change is still not established and the
  file is never opened.

## 4. Evidence classes

| Class | Items | Bound to |
|---|---|---|
| **implementer_executed** | 421-test suite from a read-only staged copy (before/after/staged/staged-after fingerprints equal, exit 0, 0 skipped, raw log `docs/evidence/tests_sixth_remediation_full.log`, hash-listed in the prompt); `prefix_check` of the 41 new tests against the pre-fix source; effective capability policy from the real CLI; v1/auth integrity output and lock verification; model-free CLI observations (`kind: model_free_cli_observation`); generated diffs | source hash `05f6bd6c…3438`, CLI 0.162.1, sandbox backend recorded in each record |
| **source_observation** | structure of one historical live-attack rollout (read, not executed) | source hash, CLI 0.162.1 |
| **historical_report** | earlier smoke/attack results, earlier evidence records (`docs/history/evidence_remediation{1..5}`) | `docs/history/` |
| **assumption** | §6 | – |

## 5. Test commands and results (actual)

```powershell
cd C:\AI\gpt-claude-mcp-bridge-v2
.\.venv\Scripts\python.exe -B tools\record_evidence.py fingerprint
.\.venv\Scripts\python.exe -B tools\record_evidence.py policy
.\.venv\Scripts\python.exe -B tools\record_evidence.py integrity
.\.venv\Scripts\python.exe -B tools\record_evidence.py cli --rollout <historical rollout>
.\.venv\Scripts\python.exe -B tools\prefix_check.py --snapshot .baseline_post_remediation5 --module tests.test_review6 --guards <7 declared>
.\.venv\Scripts\python.exe -B tools\record_evidence.py tests --label sixth_remediation_full
#  -> OK: 421 tests in 550.5s; failing=[]; problems=[]
```

* **421 tests, 0 failures, 0 skipped**, recorded against `05f6bd6c…3438`. By module:
  `test_review2` 65, `test_review3` 51, `test_review5` 50, `test_remediation` 48, `test_service` 43,
  `test_review6` 41 (new), `test_review4` 40, `test_apply_patch_attack` 36,
  `test_config_argv_auth` 18, `test_state` 18, `test_server_admin` 11.
* **Do the new tests detect the defects?** Run against the pre-fix source (the frozen
  `.baseline_post_remediation5` `bridge/` and `tools/` plus the current harness), overlay verified:
  **34 of the 41 new tests fail or error; 7 pass by declared design** (regression guards: a failing
  test is `FAILED` not `INVALID`; a single exact command is still attributed; a durable uncertainty
  still allows the pending marker; the original exception stays the cause; a complete prefix chain
  is accepted; a lone raw CR in a quoted literal is rejected - which the S8 version of this test
  had failed to isolate; ordinary whitespace is accepted). **Of the 34: 29 are behavioural
  assertion failures and 5 fail only because the new API does not exist yet** (`Events.finish`,
  `state.is_pure_thread_debt` / `conflicting_persistence_record`, `turn_problems(expected_thread=)`,
  `prefix_logic`) — weaker evidence, kept distinct in
  `docs/evidence/prefix_check.json` (`failure_kind_by_test`). Mutation sensitivity of *older* tests
  was not re-checked.
* **Process notes:** the first full run of this round had 2 failures, both builder-test
  expectations that named the previous round's files; updated. The recorded run had no concurrent
  work.
* **v1 integrity:** `v1_files`, `v1_listing`, `v1_state`, `v1_venv` unchanged; lock verified (30
  packages). `codex_stat.config.toml` differs from the original baseline (size 3348 → 3402, mtime
  2026-10-13 08:00:01Z) but did not change during this round; `auth.json` unchanged.
* **Machine state:** `sandbox_provisioning_at_record_time` is recorded in each record (`failed`
  for this run): the known sandbox-refresh lock, irrelevant to the mocked tests and the reason no
  live attack/smoke check was attempted.

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
| what host git does | `gitsafe` flags + scrubbed env + minimum version; the N1 control requires textconv, clean AND fsmonitor to fire | `$GIT_DIR/info/attributes`; git bugs |
| shell / patch tools | read-only sandbox + approval policy (argv-pinned) | live rejection for this hash not re-run |
| preflight `codex sandbox` | model-free stages; `findstr` with name as data | not the same path as `codex exec` |
| MCP/plugins/hooks/apps/browser/computer use | feature gates + per-server disables (snapshot-scoped); post-audit unexpected servers reported | allow-listed servers; no deny-all; race after the last recheck |
| delegation, image generation | feature gates (resolved) | runtime removal unverified |
| web | `web_search="disabled"` (requested) | no readout |
| processes started by the Windows sandbox service | operator reconcile after timeout/cancel/bridge death | cannot be enumerated or confirmed gone |
| recovery state against late writers | fence + compare-and-merge + serialised markers + frozen events | a writer that bypasses `Registry` (none in the package; an external process editing the files) |
| test provenance | read-only staged copy + staged before/after + live before/after fingerprints + retained log + per-test accounting | environment (PATH, `%TEMP%`, venv, CLI) not staged; rewrite-and-restore during a run is not detected |

## 7. Remaining unverified (explicit)

1. Live tool inventory under the policy (delegation/image/web actually absent?).
2. Whether `web_search="disabled"` removes `web__run`.
3. Live `--json` item shapes for failed commands.
4. The `findstr` stage, the suspended start and the target-validating resolver against the
   real launcher on a clean machine, including a real symlink/junction (the test simulates the target).
5. Attack tests, smoke test (including the attributing oracles' grammar) and the MCP stdio/service
   path for **this** source hash; in particular what shapes a real model's recorded shell calls take.
6. That the real engine's denial message prints a path form the exact comparison recognises.
7. Behaviour on the `unelevated` backend through `codex exec`.
8. Service-spawned sandbox processes after timeout/cancel/bridge death.
9. Any CLI version other than 0.162.1; non-Windows platforms.
10. Mutation sensitivity of tests older than this round (other than the N1 control).
11. Whether `--ignore-user-config` would change the MCP set (not observable model-free).
12. The cause of the `config.toml` size/mtime change (round 5).

## 8. Sanitized telemetry examples (fake CLI; fixture values)

`docs/telemetry_examples_remediation.jsonl` — 17 records regenerated in round 4 with the fake CLI
(no model call); this round's behaviours are covered by tests, not by new example records.
Content-free by construction.

## 9. Decisions (all pending; implementing a conservative candidate is not approval)

| # | Decision | GPT's recommendation (rounds 2–5) | State in this candidate |
|---|---|---|---|
| **D1** | MCP/plugins/hooks/apps/browser/computer use; `--ignore-user-config` | retain the empty allow-list; investigate the flag as a separate design change with backend selection and fresh/resumed evidence; the final-load race is a human risk decision | empty default; the flag is recorded, not adopted; the race is documented, not closed |
| **D3** | Activation style | **defer while High findings remain**; then side-by-side with edits paused | nothing registered |
| **D8-web** | `web_search` | keep disabled | disabled, no exception mechanism |
| **D8-delegation/image** | gates | keep disabled pending trustworthy runtime evidence | disabled (resolved) |
| **P2** | service-spawned commands | conservative reconciliation, with late callbacks fenced | implemented; late writers cannot undo a settlement or reconcile |
| **P3** | `degraded` mode | retain removal | removed |

## 10. The verification prompt

`docs/gpt_verification/verification_prompt.md` + `verification_manifest.json` (exact sizes, per-file
hashes, `prompt_sha256`, per-file `scrubbed_local_identifiers`). Built by
`tools/make_verification_prompt.py`, which refuses unless every embedded evidence record
(fingerprint, policy, integrity, CLI, prefix check, tests) is bound to the current hash, the test
record was executed from a staged copy that was re-fingerprinted after the run, its retained log
accounts for every test (unique, paired), and the prefix chain is RECONSTRUCTED from the module's
syntax tree, the raw log and the bytes on disk; it imports nothing able to start a process or
open a connection. Files RAW-BYTE identical to the version GPT verified last round are
hash-listed, not repeated, except those the round-5 review named as missing material (all
embedded in full again). The round-5 prompt is archived in `docs/history/gpt_verification_round5/`.
This round's own manifest cannot be embedded (it contains the prompt's hash); the prompt says so.

**Status: prepared, NOT sent.** Sending consumes subscription allowance and needs the human's
explicit authorisation each time. The sender is `tools/send_verification.py` (reviewed source:
acknowledgement flag, trusted git, one attempt, `runner.turn_problems` validity predicate).

## 11. Proposed opt-in live checks and activation/rollback — **NOT EXECUTED**

Preconditions: ChatGPT/Codex apps closed, `Get-Process node_repl` empty, human authorisation for
allowance use. Each is bound to the hash it runs against.

1. `python tools\record_evidence.py fingerprint` (confirm the hash is unchanged).
2. `bridge_admin.py status --sandbox` on a disposable repo — exercises the `findstr` stage.
3. `python tools\live_smoke_test.py --i-understand-this-uses-subscription-allowance` — 2 turns
   (the oracles attribute single simple commands from the recorded rollout; this harness has never
   been run in this form, and unobserved call shapes are INCONCLUSIVE).
4. `python tools\apply_patch_attack_test.py` (stage A), then `--live …` (stage B, 2 turns).
   Stage B covers production argv + runner, not the MCP/service path; the oracle is
   wrapper-attested and strict; completion uses the shared validity predicate.
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
`docs/remediation6.diff` + `_stat.txt` + `remediation6_src.diff`, `docs/remediation*.diff`,
`docs/evidence/*` (incl. `.log`, `prefix_check.*`), `docs/gpt_review/` (round 1),
`docs/gpt_verification/` (round-5 review, request, manifest; the prepared prompt),
`docs/telemetry_examples_remediation.jsonl`, `docs/history/`, `.baseline_*` (diff bases).
