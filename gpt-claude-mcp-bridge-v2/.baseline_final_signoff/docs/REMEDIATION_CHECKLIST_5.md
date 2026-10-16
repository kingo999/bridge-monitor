# Remediation checklist 5 (fifth round: GPT verification findings S1-S15)

Trigger: GPT's verification review of the fourth remediation,
`docs/gpt_verification/gpt_verification_20261013T080140Z.md` (no Critical finding; defer activation;
R1 and R13 NOT CLOSED, eight others PARTIALLY CLOSED; cross-cutting claims A-D CONTRADICTED; new
findings S1-S15, 5 High). Authorised by the human: "yes remediate S1-S15". Same boundaries: isolated
v2 only, no live model calls, no registration, no activation, no sending (a separate authorisation is
required for the round-5 send).
Diff base: `.baseline_post_remediation4/` (exactly the state GPT verified; hashes and hash scopes in
its `MANIFEST.json`).

Process note: the list was written while implementing. Each new test in `tests/test_review5.py` was
run against the frozen snapshot with `tools/prefix_check.py`, which now also verifies the overlay
against the snapshot manifest, classifies failures (assertion / missing API / behavioural error) and
refuses loader errors; see REVIEW_PACKAGE §5 for the numbers.

## Decisions that shaped several findings

| Decision | Why |
|---|---|
| **A disposition command settles a THREAD, never a process fact** (S1). `reset`/`adopt` keep every in-flight record except a pure `persistence_failed` thread debt, including a raw `running` record nobody has converted yet. | Round 4 treated "not literally `uncertain`" as "no process uncertainty", so a raw dead-owner record was deleted and the call became runnable without `reconcile`. |
| **A settled disposition is a durable fact on the record** (S2): `thread_settled=<thread id>` (+ how/when). `thread_debt` honours it; the observed `thread_id` stays for audit. | Clearing `unbound_thread` while keeping `thread_id` let the next health check or `reconcile` derive the debt again. |
| **Nothing downstream behaves as though a write succeeded unless it did** (S3). The primary uncertainty write is retried, checked, and its failure raises `uncertainty_marker_unwritable` before any pending marker is mirrored. | `update_marker`'s Boolean was ignored. |
| **Evidence is complete only if the reader reached EOF** (S4). A reader alive after its bounded join (or that never reached EOF) marks the evidence incomplete, and the events object is sealed. | Reader failure was only recorded for an exception. |
| **Attribute an OPERATION, not text** (S5). Smoke oracles accept only native function calls with structured arguments or a strict one-call pass-through wrapper; a write is matched by its verb and target, a refusal only if the command did not supply the wording. | Printing `git rev-parse HEAD <sha>` or `canary.txt TAMPERED rejected` satisfied substring matchers. |
| **System tools fail closed** (S7): no `SystemRoot`/`windir`/hard-coded fallback; the tool is unavailable. | The fallback reinstated the trust source the OS query exists to avoid. |
| **The sender is part of the reviewed source** (S14): `tools/send_verification.py` with an acknowledgement flag and the package's own `runner.turn_problems`. | The previous sender was an outside script with a private, weaker validity predicate. |

## Work items

| Finding | Change | Proof |
|---|---|---|
| S1 | `admin._settle_unbound_thread`: only `persistence_failed` is removed; every other record is updated in place; retained-record note; settlement failure reported (exit 1) | `S1_*` |
| S2 | `state.thread_debt` honours `thread_settled`; `pointer_bound` case covered because settlement is recorded at reset time | `S2_*` |
| S3 | `_check_session_health`: `update_marker` retried and checked; `uncertainty_marker_unwritable` raised, no pending write; runner_failed marker write retried | `S3_*` |
| S4 | `Events.stdout_eof`, `seal()`; `run_turn` marks `reader_failed` when the reader is alive after the join or never reached EOF; join timeouts are named constants; `runner.turn_problems` | `S4_*` |
| S5 | `tools/smoke_oracles.py` rewritten around attributed `Operation`s | `S5_*` |
| S6 | `_gather_text_ex`: request keys skipped only outside returned containers; all JSON string escapes resolved; window 4096; `CreateProcess\s{0,1024}\{` | `S6_*` |
| S7 | `execpath.system_tool` raises `SystemDirectoryUnavailable`; callers already treat that as "tool unavailable" | `S7_*` |
| S8 | `_JS_RESERVED` rejected as identifiers; raw CR excluded from quoted literals; template-literal CRLF normalised | `S8_*` |
| S9 | staged files read-only; staged copy fingerprinted again after the run; `run_problems(staged_after=)`; `cmd_tests(root=, evidence=)` for functional tests | `S9_*` |
| S10 | `prefix_check`: overlay verified, expected count, loader errors, failure classification; builder `prefix_problems` re-verifies the chain | `S10_*` |
| S11 | one `abort_before_exec` for every proven-no-execution refusal; fallback persistence checked and reported; failed `settle_failed_turn` clear keeps the record blocking | `S11_*` |
| S12 | `ArchitectService._attach_warnings`: categorised errors carry the recorded banners | `S12_*` |
| S13 | `registry.prev.json` is written with the FIRST registry write too; the lifecycle journal is a second witness | `S13_*` |
| S14 | `tools/send_verification.py`, `runner.turn_problems`; the sender is embedded as source | `S14_*` |
| S15 | persistence banner registers its exact code; `_end_maintenance_reporting` on every admin path | `S15_*` |
| also | `run_simple` completeness (`truncated`, `reader_incomplete`, `complete`) enforced by version/auth/catalog/features/mcp readouts; `allowed_servers` accumulates the discovery list too; N1 control states what it demonstrates; R4 tautology removed; bare-`git` scan covers every tool and test | `Also_*` |

## Not done / not authorised

Live model calls (including sending the round-5 prompt), registration/activation, D1/D3/D8. The MCP
configuration race (no deny-all in CLI 0.162.1) remains documented, not closed. The npm launcher's own
executable selection beyond the artifacts `resolve_cli` validates (`node.exe`, `codex.js`) is not
examined and not claimed. `CreateProcess` followed by more than 1024 whitespace characters before `{`
is outside the bounded signature (documented trade-off for linear-time scanning).
