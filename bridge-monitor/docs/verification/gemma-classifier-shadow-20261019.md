# Gemma 4 12B classifier shadow — verification, 19 October 2026

Scope: advisory classification only. No live routing, policy, schema, service or scheduled task was
changed. Raw evidence is in the git-ignored `var/probes/gemma-classifier-shadow-20261019/`.

## Starting state (read-only checkpoints)

- Active operational policy **`43a200ea7bf9f73a…`** (full hash `43a200ea7bf9f73aad0cd5f642e5b51230ffd405f275342a81bc911b906770b2`,
  activated 19 Oct 15:52 under approval `user-resolve-shadow-envelope-2026-10-19`). The operations
  document still names `75116df3…`, the earlier 15:13 activation; this work did not reactivate either.
- Migrations through `006_router_shadow` applied; no `classifier_*` table in the operational database.
- No busy, queued or uncertain jobs; 6,975 events. Counts, policy, migrations and job tables were
  identical in `checkpoint-before-edit.json`, `-mid-build.json` and `-after-runtime-tests.json`.
- Port 11435 was closed and no Ollama process existed. Runtime SHA-256 `76604706…f960`; manifest
  SHA-256 `4eb23ef1…b05c`, both verified.
- Other sessions were editing the shadow-worker files and committing the shared tree throughout
  (commits titled "shadow policy" swept in this work's files without a request from me). Those files
  were not touched here.

## What was built

Lifecycle (`scripts/gemma-runtime.py`), strict client/contract/policy (`bridge/classifier/`,
`contracts/classifier.py`), default-off capture and single-flight runner, migration 007 with manual
rollback, authenticated cohort endpoints and a self-contained dashboard panel, replay tooling, and
91 focused tests. See the design and operations documents.

## Demonstrated on the RX 7900 XT

- Discrete Radeon RX 7900 XT selected through ROCm `gfx1100` (server log, `type=discrete`; integrated GPU dropped).
- `/api/ps` after load: `gemma4:12b`, digest `4eb23ef1…`, size = size_vram = 8,373,969,878 B (7.80 GiB),
  context 8192, i.e. fully in VRAM. `status` reported `ready`.
- First load after start: 7.02 s; after a stop/start cycle: 6.29 s. These include setup and
  OS-cache effects, not pure disk time. No separate cold-disk measurement was made.
- Resources (approximate, other software running): dedicated GPU memory 12.59 → 3.77 GiB on stop
  (**8.8 GiB**); free RAM 31.6 → 39.1 GiB (**7.6 GiB**); the runner child held ~7.3 GiB working set. Matches the
  October 18 measurement. Gaming, rendering and sustained IDE/build contention were not tested.
- Stop terminated the owned runner child and server, freed the port and archived its receipt.

## Reference replay: the original 32 cases, seeds 42 and 43

Cases and labels are the unmodified `cases.json` (SHA-256 `fd86adc4…1609`, unchanged).

| Measure | Pass 1 | Pass 2 |
| --- | ---: | ---: |
| Strict contract valid | 32/32 | 32/32 |
| **Raw** route matches reference | 30/32 | 30/32 |
| Raw category matches | 29/32 | 29/32 |
| **Post-policy allowed** recommendations | 30/32 | 30/32 |
| Allowed **and** matching reference | 30/32 | 30/32 |
| Recommendations the policy blocks | 2 (C08, T08) | 2 |

Raw accuracy and policy-allowed counts are separate measures that happen to match numerically here
(30 of 32 each) because the same two cases, C08 and T08, are both wrong and blocked. C08 (`code_local`, worker unavailable and paid prohibited; reference
`clarify`) is blocked with `code_worker_unavailable`; T08 (`general_local`, general worker
unavailable; reference `paid`) is blocked with `general_worker_unavailable`. Both stay recorded as raw model
misses. They are preserved as regression fixtures (`tests/fixtures/classifier-regressions-20261018.json`).
All 64 recommendations were **identical, including reason text,** to the October 18 AMD responses
(`parity-vs-historical-amd.json`), so this client reproduces the benchmark.

Warm latency (64 calls, HTTP wall time): median **1.556 s**, p95 **1.715 s**, first content 0.770 s median.
Historical 7900 XT values were 1.575 s and 1.729 s (about 1% lower now); not used as a pass threshold.
Tokens: 39,766 input / 2,802 output, all reported by the server.

## Small held-out set (18 cases, labels frozen before inference)

Frozen hashes: `frozen-labels.json`. Covers complete/incomplete context, tests present/absent,
ambiguous intent, security-sensitive work, multi-file work, unavailable workers, an embedded
instruction, deterministic work and research. Seeds 42/43, identical routes in both passes.

- Contract valid 36/36; **raw route acceptable 13/18** each pass; category 17/18; policy-allowed 13/18.
- All five misses were blocked by the policy: H02, H03 (`code_local` with the code worker unavailable),
  H10, H12 (`general_local` with the general worker unavailable), H04 (`code_local` with no trusted tests).
  In 4 of the 5 availability-adverse cases the model ignored the stated fact; it
  respected it only in H17, where the change was also security-sensitive.
- All 13 correct recommendations were allowed. The embedded-instruction case (H13) was ignored.
- Warm median 1.592 s, p95 1.790 s (n=36, slightly longer prompts).
- This is 18 hand-written scenarios and shows a failure mode, not broad reliability. Because the policy also blocks
  tests-absent `code_local`, "allowed" and "correct" can coincide here by construction.

**Reproducibility caveat.** In the integration demo, the same H10 input and seed returned `paid`
where the replay returned `general_local`, and prompt token counts differed by one (cache state). Greedy
settings did not give run-order-independent output even on one machine. Do not treat any single
recommendation as deterministic.

## Isolated end-to-end integration

A throwaway database (`bridge_monitor_test_gemma_e2e_20261019`, still present; drop deliberately)
was migrated from scratch (001–007). Five real controlled router case files from `var/router/incoming`
(read only), a deliberate duplicate, an explicit `--replay-id` rerun, and four held-out items were captured,
then the real runner classified all 10 on the 7900 XT through the CLI.

- Duplicate re-capture returned `duplicate`; the replay-id rerun created a second observation.
- 10/10 completed, 10/10 `gpu_resident`, tokens all server-reported, 0 failed/skipped/unknown.
- 8 `code_local`, 1 `tools`, 1 `paid`; 9 allowed, 1 blocked (`code_worker_unavailable`, H03). Reference labels: 4, raw acceptable 3.
- Actual route unknown for all 10 (these cases have no work item); the view reports unknown, not a guess.
- Production-side tables in that database (`events`, `calls`, `runtime_jobs`, `delegation_decisions`,
  `results`, `usage_observations`) stayed at 0 rows.
- Unauthenticated cohort request → 401; the response body is retrievable only through the operator content endpoint.
- Median queue wait 12.4 s only because all inputs were captured before the run; request 1.61 s.
- With the server stopped, `run --until-idle` claimed and sent nothing (`unavailable`), leaving the input `captured`.

## Tests

`tests/classifier`: 91 tests. Together with `tests/contracts`, `tests/router`, `test_router_queries`,
`test_metrics`, `test_auth`, `test_content` and `test_snapshot_stream`: **184 passed** against the dedicated
`bridge_monitor_test` database. The full repository suite was not run. They cover: disabled feature (no
model call, no rows), every contract failure class, timeout/cancel/partial stream/oversize/HTTP errors,
wrong model and digest, CPU/partial offload, busy queue, duplicate and restart behavior, hostile
source text, C08/T08 and unknown-fact policy, no dispatch side effects (table counts unchanged, no
dispatcher import, router sources never mention the classifier), production metrics and the Qwen
cohort unchanged, migration/rollback in an isolated database with record preservation, immutability and
append-only triggers, auth, and launcher ownership (an unrelated process, a reused PID and a foreign
port are never touched). The dashboard type-checks (`tsc --noEmit`); it was **not rendered in a browser** and
`dashboard/out` was not rebuilt.

## Limitations and unknowns

- No paid savings, avoided calls or electricity cost are established; they are reported as unknown.
- Availability facts are a reviewed configuration assertion plus a read-only Qwen-capacity check, not a live Skynet probe.
- Model output cannot enforce availability or permissions: it ignored the stated fact in 6 of the 7 availability-adverse
  cases across both sets (C08, T08, H02, H03, H10, H12), and only the policy layer caught them.
- The October 18 mixed benchmark's runner exited with an error because the local server had been stopped before its
  final metadata query; all workflows, baselines and hashes were saved and a separate audit passed. This work did not rerun it.
- Skynet and Qwen were not used. No paid call was made.

## Final state

Gemma server stopped, port 11435 closed, no `ollama`/`llama-server` process. Live policy, migrations,
job tables and event counts match the starting checkpoint. `BridgeMonitor-RouterShadow` was not touched.
