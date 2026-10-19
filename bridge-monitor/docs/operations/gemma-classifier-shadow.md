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

Needs no PowerShell 7 and no global change. `start` verifies the pinned `ollama.exe` SHA-256 and the
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

Steps 1–3 were applied on 19 October 2026 after explicit approval (see the verification report). Step 4 is the
manual operating mode; step 5 is still not done. Each step changes live services or schema and needs a deliberate go-ahead.

1. Take the standard backup (`scripts/backup-monitor.ps1`), then confirm 007 is the only pending
   migration. Apply with `python -m database.migrate <OPERATIONAL_DSN_ENV_NAME>`. It is additive:
   two new tables, two guard triggers, no change to existing objects.
2. Restart the dashboard backend so the two endpoints exist. Before step 1 they answer
   "schema not applied"; after it they return data. Unauthenticated requests stay 401.
3. Rebuild the static dashboard (`npm run build` in `dashboard/`) to show the new panel; it
   renders nothing while the schema is absent.
4. Start Gemma with `start --warm`, then run capture/run commands manually. No scheduled task is
   created and none of this touches `BridgeMonitor-RouterShadow`.
5. A `route_work` capture hook is **not** implemented. If wanted, add it after review as a
   try/except-wrapped call to `bridge.classifier.ingest.capture_text` that is awaited by nothing.
   Rollback is removing that call.
