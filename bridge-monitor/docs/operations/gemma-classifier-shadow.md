# Gemma classifier shadow operation (RX 7900 XT)

The classifier is **advisory and observational**. Gemma 4 12B recommends one of `tools`,
`general_local`, `code_local`, `paid` or `clarify` for a request. The recommendation is stored
beside a deterministic check of whether it *would be allowed*, and beside the route the
unchanged system actually took. It never selects, dispatches, admits, rejects or alters
anything, and it is not wired into `route_work`, the scheduler, the Qwen shadow worker, or any
policy. Only inputs you explicitly hand to it are observed; ordinary conversations are not.

Everything is off until you copy `config/gemma-classifier.example.toml` to a `*.local.toml`
(git-ignored), set `enabled = true`, and pass it with `--config` (or
`BRIDGE_CLASSIFIER_SHADOW_CONFIG`). The commands below use
`var/probes/gemma-classifier-shadow-20261019/classifier-local.toml` as the config.

## Runtime: start, status, stop

```powershell
.\.venv\Scripts\python.exe scripts\gemma-runtime.py start --warm --config <config>
.\.venv\Scripts\python.exe scripts\gemma-runtime.py status --config <config>
.\.venv\Scripts\python.exe scripts\gemma-runtime.py status --probe --config <config>
.\.venv\Scripts\python.exe scripts\gemma-runtime.py stop --config <config>
```

For everyday use, double-click `scripts\gemma-start.cmd` (starts and loads the model) or
`scripts\gemma-stop.cmd` (frees the GPU, e.g. before gaming); both are safe to repeat and use the
default runtime settings. Needs no PowerShell 7 and no global change. `start` verifies the pinned `ollama.exe` SHA-256 and the
`gemma4:12b` manifest SHA-256, refuses an occupied port, then launches the portable Ollama 0.30.9
detached with a **process-scoped** environment: `127.0.0.1:11435` only, cloud disabled, one
parallel request, one loaded model, a bounded request queue, 8192 context, configurable
keep-alive, a private profile directory, and fresh timestamped logs under `var/logs/gemma-classifier/`.
A launcher receipt (PID, image path, creation time in 100 ns ticks) is written exclusively to
`var/run/gemma-classifier/current.json`; receipts are archived as `stopped-*`/`stale-*`, never
overwritten. Historical benchmark receipts are not touched.

`status` reports process ownership, HTTP, model digest, and GPU evidence separately. `ready`
requires an owned process, the pinned manifest digest, the server log naming the discrete
`ROCm`/`gfx1100` device, **and** `/api/ps` showing the model fully in VRAM. Before the model
is loaded it reports `up_gpu_not_demonstrated`; `--probe` (or `start --warm`) loads it.

| Exit | Meaning |
| ---: | --- |
| 0 | ready (GPU residency demonstrated) |
| 1 | problem: wrong digest, identity mismatch, failed start |
| 2 | up, but GPU offload not demonstrated (not resident, partial offload, CPU only) |
| 3 | stopped |
| 4 | the port is held by a process this launcher does not own |
| 5 | refused: receipt does not match the live process |

`stop` acts only when PID, executable path and creation time all match the receipt. It unloads
the model from this endpoint, ends the runner child processes that live under the runtime
directory, ends the server, confirms the port is free and archives the receipt. An unrelated
listener, a reused PID or a replaced executable is refused. Stop the classifier runner first.

## Capture, run, replay

```powershell
# Capture a pre-solution router case file at task arrival (before route_work runs)
.\.venv\Scripts\python.exe scripts\classifier-shadow.py --config <config> --dsn-env <ENVVAR> capture-case --case var\router\incoming\<case>.json [--work-item-id <id>]
# Capture arbitrary text with trusted facts
.\.venv\Scripts\python.exe scripts\classifier-shadow.py --config <config> --dsn-env <ENVVAR> capture-json --input request.json
# Classify queued inputs, one at a time
.\.venv\Scripts\python.exe scripts\classifier-shadow.py --config <config> --dsn-env <ENVVAR> run --until-idle
```

`--dsn-env` names an environment variable holding a DSN. There is no default, so the operational
database is never chosen implicitly. Identity is `(origin, origin_ref, request/source hashes,
profile, rubric, model digest, replay_id)`: repeating a capture returns `duplicate`; an intentional
rerun needs a new `--replay-id`. A full queue (`max_queue`) or an input over `max_input_bytes`
records a `skipped` row with its reason and nothing slows the caller. A runner restart marks any
half-sent row `uncertain` and never resends it. Unprocessed rows expire as `expired_unprocessed`
rather than being classified late. If the endpoint or model digest is wrong the runner claims and
sends nothing. One runner at a time is enforced with a database advisory lock. A CPU or partial
offload observation is recorded and halts the runner (`halt_on_cpu_fallback`).

`capture-case` facts come from reviewed config plus a read-only check of held or uncertain Qwen
capacity; `general_available` is `false` because this project has no general local worker. They are
never inferred from model text. They are a configured assertion at capture time, not a live
probe of Skynet.

File-based replay (no database) of any labelled case set:

```powershell
.\.venv\Scripts\python.exe scripts\classifier-shadow.py --config <config> replay --cases <cases.json> --seeds 42,43 --out var\probes\<fresh-dir> --label <name>
```

The output directory must not exist. It receives `manifest.json`, `warmup.json` (model load timed
apart from warm calls), `responses.jsonl` (raw text, parsed result, policy verdict, scores) and
`summary.json`.

## Inspect

- Dashboard backend: `GET /api/v1/classifier/cohort` and `/api/v1/classifier/observations/{id}`
  (operator auth). Request, source and response bodies are fetched only through the existing
  `/api/v1/content/{content_id}` endpoint. Until migration 007 is applied the cohort answers
  `{"available": false, "reason": "schema_not_applied"}`.
- The cohort counts submitted/attempted/completed/failed/skipped/unknown, latency and queue
  distributions, token totals with provenance (`ollama_terminal` versus `estimated_...`), the
  recommendation distribution, policy-blocked counts and reasons, reference-label agreement, how
  often the recommendation differs from the actual route (a difference is not an error), and
  verification/correction coverage. It is separate from production routing totals and from the
  Qwen coding-quality cohort. Hypothetical avoided calls, electricity and savings stay unknown.
- Record judgments with `classifier-shadow.py evaluate`; evaluations are append-only. A
  correction supersedes (`--supersedes`) instead of rewriting.

## Disable, diagnose, roll back

- **Disable**: set `enabled = false` (capture and the runner become no-ops and make no model
  call), stop the runner, then `gemma-runtime.py stop`. The authoritative path never needed it.
- **Diagnose**: `status` first. `up_gpu_not_demonstrated` after `--probe` means CPU or partial
  offload; read `var/logs/gemma-classifier/ollama-*.stderr.log` for the `inference compute`
  line. `model_digest_mismatch` means the model store changed. Rows stuck `captured` mean the
  runner is stopped or the endpoint unavailable. `uncertain` rows are real sends whose outcome is
  unknown.
- **Roll back the schema** (only after a backup, and only if classifier data may be discarded):
  `database/rollbacks/007_classifier_shadow_down.sql`.

## Operational rollout

Steps 1–3 were applied on 19 October 2026 after explicit approval (see the verification report).
Step 4 is the manual operating mode; step 5 has begun as a controlled rollout. Operations that
change live services or schema require authorization covering those changes.

1. Take the standard backup (`scripts/backup-monitor.ps1`), then confirm 007 is the only pending
   migration. Apply with `python -m database.migrate <OPERATIONAL_DSN_ENV_NAME>`. It is additive:
   two new tables, two guard triggers, no change to existing objects.
2. Restart the dashboard backend so the two endpoints exist. Before step 1 they answer
   "schema not applied"; after it they return data. Unauthenticated requests stay 401.
3. Rebuild the static dashboard (`npm run build` in `dashboard/`) to show the new panel; it
   renders nothing while the schema is absent.
4. Start Gemma with `start --warm`, then run capture/run commands manually. No scheduled task is
   created and none of this touches `BridgeMonitor-RouterShadow`.
5. The optional `route_work` capture hook entered controlled rollout on 19 October 2026.
   Migration 008 and dashboard reporting are deployed, and one fresh parent passed a real
   controlled capture. The Bridge Monitor desktop MCP entry now names the reviewed enabled
   local configuration for subsequent connections. Existing parent connections keep their
   startup snapshots until reconnect. See the [rollout receipt](../reviews/gemma-route-work-capture-rollout-2026-10-19.md).
   A synchronous call to manual `capture_text` is not an equivalent replacement.

## Optional arrival capture

The parent can offer registered, matching single-file cases before paid dispatch. This does
not select a route or start Gemma. Child and shadow audiences have no observer. Only the
existing fresh-case load supplies data: request, baseline and bounded registration metadata;
hidden tests, expected outputs and answers are excluded. Natural/controlled origins are kept.

Both `classifier_shadow.enabled = true` and `route_work_capture.enabled = true` must be
explicitly set in a reviewed TOML file inside the project, named by
`BRIDGE_CLASSIFIER_SHADOW_CONFIG` before parent startup. Manual-only configuration leaves the
hook off; an inherited path outside the project is quietly ignored without reading that file.
Configuration and explicit paid/tools assertions are frozen at startup. The hook
records general capacity false and code capacity unknown; it performs no delayed capacity
probe. The model, digest, profile and endpoint remain pinned to the reviewed values.

The observer has one daemon worker, eight pending items and a non-waiting offer. It drops
oversize, contended, full or expired inputs rather than persisting rejection bodies. Limits
are 24,576 combined request/source characters and serialized input bytes, 256 characters per
copied identifier, 4 KiB provenance, and 600 seconds age from arrival. Automatic database
operations request `connect_timeout=1`, with a 50 ms lock timeout and 250 ms statement timeout.
Installed Psycopg 3.3.6 enforces a two-second connection minimum per address, so the effective
connection bound is not one second. The project's numeric loopback DSN uses a single address;
multi-address/DNS and OS stalls can take longer. Shutdown discards pending
items and joins the active daemon for at most 250 ms; an active transaction may still finish.

Migration `008_classifier_route_capture.sql` adds only classifier accounting/progress tables
and a partial classifier index. Missing schema disables the optional sink on its first
attempt; it does not affect parent dispatch or the existing manual classification schema.
It was applied to the operational database on 19 October 2026 after a private verified backup;
only 008 was pending, and production counts and the active policy stayed unchanged. The database-wide lifetime limits
are 256 hook observations and 4 MiB serialized input, and one successful persistence per two
seconds. Duplicates are free. Observation, protected content and budget charge commit together.
Restart, profile change and rollback never replenish counters. A database error or ambiguous
commit disables that process's sink, discards its pending items and is never retried. Known
pre-commit failures count as `database_error`; an error during commit after writes counts as
`persistence_uncertain`. Even a short pre-write lock timeout latches off: this deliberately
trades coverage for failure isolation, rather than silently relaxing the approved design. Budget
exhaustion also latches the sink off. Replenishment/retention changes require separate approval.

Process-local fixed counters include offer/queue/contention, expiry, size, cooldown/budget,
binding and persistence losses. They are approximate and disappear at exit; persisted rows
are not a denominator for all requests. Fixed reason diagnostics go to stderr from the worker,
never through the production emitter. Memory queues do not survive restart. Arrival capture
can consume lifetime budget even if later decision publication, admission or
delivery fails. Missing links and failed dispatches do not refund that budget. The cohort's
capture-method filter, unique/overlapping work items and fact coverage cover only its bounded
query window. Unknown code-capacity blocks are shown separately from other policy blocks;
neither proves the raw model recommendation was incorrect.

Hook links require the exact frozen decision/call/task/work-item identities and gate hashes,
case reference and contract. Missing, mismatched and unmapped decisions leave SQL NULL;
there is no substitution of another decision from the same work item. Existing historical
non-NULL values, including `unknown`, are never overwritten. Manual captures retain their
existing immediate matching behavior and are not automatically enrolled in hook reconciliation.

Each manual runner iteration reconciles at most 64 completed, unlinked hook rows before its
endpoint availability check. A saved ordered cursor advances even over missing/mismatched
decisions and wraps at a fixed sweep upper key, revisiting unresolved/backdated completions.
Route/provenance/time fills and cursor progress commit atomically. `runner.link_progress`
exposes examined/linked/error/timeout/suspended counts to local callers; fixed stderr reasons
report failed or suspended passes. Two consecutive timeout failures suspend reconciliation for that
invocation; missing/corrupt schema suspends it immediately. Investigate database health/schema
and inspect progress with a read-only query, then invoke the runner manually again. No retry
loop, background service or model start is created. With Gemma stopped, reconciliation and
queue expiry can proceed without claiming or sending model work. A production decision is
route evidence, not proof of admission, completion or delivery.

For future rollouts, obtain authorization covering schema migration, deployment/restart and
local enablement. The migration runner applies **all** pending migrations; review that list
first and use bounded locking. First deploy with the new switch false, verify disabled
behavior, then use a small controlled case. The 19 October rollout followed this sequence
under explicit user authorization. Gemma remains stopped; starting it and performing a
manual classifier run remain separate decisions. No live policy activation is needed.

Disable future capture by setting `route_work_capture.enabled = false` and performing an
approved parent restart; editing the file alone cannot stop a running startup snapshot.
Stopping the classifier runner stops subsequent model sends, not arrival capture or an
already-sent request. Code rollback removes the optional offer/bootstrap wiring followed by
an approved restart. For a full rollback to an older code version, first remove the entire
`[route_work_capture]` section from local classifier TOML files: the old strict loader rejects
that unknown section, even with `enabled = false`, and would break manual capture/runner use.
Retain the existing manual classifier settings. Preserve observations, content, budget and cursor; do not run migration
007's destructive rollback with dependent 008 objects present. No policy change or schema
rollback is required to disable the feature.
