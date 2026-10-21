# Gemma classifier shadow operation (RX 7900 XT)

The classifier is **advisory and observational**. Gemma 4 12B recommends one of `tools`,
`general_local`, `code_local`, `paid` or `clarify` for a request. The recommendation is stored
beside a deterministic check of whether it *would be allowed*, and beside the unchanged
system's recorded route decision. It never selects, dispatches, admits, rejects or alters
production work. Its recommendations do not enter `route_work`, the scheduler, the Qwen
shadow worker or live policy. The optional arrival hook records matching registered cases;
classification still requires an explicit runner invocation. Ordinary conversations are not captured.

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
4. Start Gemma with `start --warm`, then run capture/run commands manually, or use the explicitly
   installed automatic task below. Neither mode changes `BridgeMonitor-RouterShadow`.
5. The optional `route_work` capture hook entered controlled rollout on 19 October 2026.
   Migration 008 and dashboard reporting are deployed, and one fresh parent passed a real
   controlled capture. The Bridge Monitor desktop MCP entry now names the reviewed enabled
   local configuration. The desktop MCP was reconnected at 21:17 CEST and the current tool
   connection passed a zero-call-budget capture/recovery check. Future configuration edits
   still require reconnecting startup snapshots. See the [rollout receipt](../reviews/gemma-route-work-capture-rollout-2026-10-19.md).
   At 21:24 CEST, the pinned runtime was started and one still-fresh controlled hook
   observation was classified and linked. That one-shot classifier runner was closed;
   continuous operation was enabled on 20 October, as described below. A synchronous call to manual
   `capture_text` is not an equivalent replacement.

## Automatic sign-in startup and recovery

`scripts/install-classifier-auto-task.ps1 -ConfigPath <reviewed-local-TOML>` prints a task plan.
Adding `-Install` explicitly registers `BridgeMonitor-GemmaClassifier`; an existing task is
never overwritten. It runs `scripts/classifier-auto.py` through the project virtual environment's
`pythonw.exe`, with the current user's limited interactive token and no visible console. It starts
30 seconds after that user signs in, including after reboot. It does not run before sign-in, matching
the project database and dashboard tasks. No account password is stored. The protected desktop
credential file is read by the worker; its contents never enter task arguments or logs.

The task allows no overlapping instances and no time limit. It requests three failure retries at
one-minute intervals; an independent, indefinite five-minute recovery trigger covers exits even
when Windows does not perform an immediate retry. Allow up to five minutes for crash recovery
while signed in and dependencies are available. A Windows-held file lease prevents overlapping supervisors across sessions,
including manual starts. The existing PostgreSQL advisory lock prevents simultaneous classifier
runners. An already-running manual runner is left alone; the supervisor waits for its lock before
starting or warming Gemma. Classification runs inside the supervisor, without a separate child
runner to orphan. A lost database lock connection is discarded and reacquired before another pass.

The supervisor waits for the exact configured loopback database and uses the existing runtime
ownership receipts, executable/model hashes and GPU checks to start or recover the project-local
Gemma server. Transient database/runtime failures retry with a 5–60 second backoff. Interrupted
classification sends become `uncertain` under the existing recovery rule and are never resent.
Queue age, size, capture budget, profile, route linking and all live routing policy stay unchanged.

CPU/partial offload, identity mismatch, an unowned listener, or a changed pinned configuration
write `var/run/classifier-shadow-auto/halt.json`. Scheduler retries do not clear this safety halt.
Review and resolve the cause before deliberately removing it; configuration changes also require
reviewing the task's pinned hash. Do not delete a halt just to make automatic retries resume.

For a deliberate pause, create `var/run/classifier-shadow-auto/stop.requested`. The worker finishes
its current bounded classification, closes the database lock, and exits. The marker survives
reboot and prevents every scheduled start. Remove only this marker and start the scheduled task
to resume. Disable the task as well when retiring the service; stopping the task alone does not
disable its recovery triggers. Gemma's runtime remains independently controlled by `gemma-runtime.py`.

`var/run/classifier-shadow-auto/service.json` records process identity, latest status and heartbeat
(at least every 30 seconds during normal operation). Check PID, executable and creation time rather
than trusting a stale receipt after a crash. The bounded, rotating supervisor log is
`var/logs/gemma-classifier/auto-service.log`; runtime launch logs retain their existing locations.
The exported installed definition is `var/run/classifier-shadow-auto/task-definition.xml`.

Status and halt files are written to a closed temporary file before atomic replacement. Windows
can briefly deny that rename while a reader or scanner holds the destination. Error codes
5, 32 and 33 receive five bounded retries at 10, 20, 40, 80 and 160 ms (310 ms total requested
delay). Only the rename is retried: no model request, classification or database write is
repeated. Persistent denial preserves the previous valid destination and raises the original
error. Other IO errors are not retried. Recovery/failure diagnostics record the operation,
Windows code and retry count without logging file bodies or exception messages. This does not
alter permissions or antivirus settings.

The task was installed on 20 October 2026. An idle-crash test restored the supervisor through
the five-minute trigger, with exactly one database lock owner. The shorter failure retry did
not fire in that test; the five-minute trigger is the verified recovery path. No additional
classification, production call, capture-budget charge or policy activation occurred during
handoff or crash recovery. Local receipts are under `var/hook-rollout-20261019/autostart-*.json`.

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
under explicit user authorization. A subsequent explicit request authorized starting Gemma
and classifying one controlled hook observation; that check completed successfully. Further
manual runs remain explicit operations. No live policy activation is needed.

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


## Normal parent traffic capture

The separately enabled `[route_work_capture].normal_traffic_enabled = true` extends
arrival capture to ordinary parent `route_work` requests of any category/contract,
without a case registration, and public parent `delegate_claude` submissions. Both
existing enable switches remain required. Existing configurations retain registered-case
capture until this third switch is explicitly enabled and the parent is restarted.

Unregistered requests copy only the original prompt and bounded call/task identifiers.
They do not read repositories, copy arbitrary caller context, hidden tests or answers,
or infer source completeness, test coverage, scope or code capacity. Those facts stay
unknown. Matching registered Python cases retain their original source and provenance.
The parent may mark a controlled probe with `task.context.shadow_capture_origin =
"controlled"`; this label cannot set trusted facts or routing authority. Other ordinary
submissions are natural. A matching controlled registration also stays controlled when
its task needs the prompt-only path.

Normal `route_work` captures remain in the route_work dashboard filter. Direct public
parent delegations appear in the additional bridge_ingress filter, with ingress_method
identifying the entry point. Internal delegation from route_work and existing-decision
recovery do not offer a second observation. Direct public delegations are distinct calls
and carry distinct call identities. Classification results never enter a response,
admission expression, worker prompt or routing decision.

The same eight-item queue, two-second persistence interval, 600-second expiry, database
failure latch, 256-row / 4-MiB lifetime quota and no-resend rules cover both capture paths.
No quota is reset. Migration 009 adds a classifier-only partial index for the expanded
late-link candidate population; it reuses the saved cursor and does not alter production
tables. Immediate and late normal links validate the exact call, task, work item, model,
original prompt hash and production mode. route_work additionally validates its exact
parent decision, contract and verifier reference. Direct delegates use the exact admitted
Claude job and its original prompt content; rejection without admission leaves the route
unknown. A known route is a recorded decision/admission, not proof of completion.

This integration observes requests submitted through Bridge Monitor. Standalone architect
or Claude MCP servers and ordinary chat are separate entry points; they are not intercepted.
Runtime supervision, the pinned Gemma generation profile, GPU safety checks and live
routing policy remain unchanged. Rollback is to disable normal_traffic_enabled and restart
the parent, preserving observations, the additive index and lifetime accounting.

## Live classifier display

The orchestration graph shows Gemma on a separate dashed advisory path. Its node pulses
only while an observation is recorded as dispatching. The advisory path animates while
captures are queued or classifying; it cannot imply a Claude or Qwen admission. Claude
and Qwen node pulses use their own recorded active-worker counts, and the Qwen path no
longer animates merely because Claude is active. These are recorded working states,
not access to a model's private reasoning. The main Codex conversation has no bridge
turn-status signal and is not shown as thinking based on an open connection.

The authenticated, read-only `/api/v1/classifier/activity` endpoint supplies queue counts
and the latest observation's state, timestamp and recommendation, without prompt, source
or response bodies. The flow polls it one second after each completed read, with a
five-second browser timeout and bounded database timeouts. Failed reads clear activity
and display "Activity unavailable" rather than retaining an active pulse. A missing
classifier schema is unavailable, not an idle zero. Last-observation text remains after
short requests finish so that completion can still be inspected; it is not live activity.
For eight seconds after a recorded completion, the advisory edge also animates with an
explicit "Result · recently completed" label. The model glow stops on completion. This
brief result indication is never counted or labelled as an active worker. Active model
nodes have a coloured outer glow; animated paths have visibly travelling dashes.

Classifier cohort statistics refresh every two seconds after a completed read. Both
cohort and activity reads have two-second connect, one-second statement and 200-ms lock
timeouts, so a stalled SQL read cannot accumulate indefinitely behind browser retries. Both
pollers stop during Pause display, abort in-flight reads on pause or unmount, and refresh
on resume while retaining classifier filters. They do not publish production events,
start model calls, warm the GPU, change quotas or modify routing policy. Browser reduced
motion preferences disable pulses and animated paths while counts remain visible.

All graph nodes distinguish working, waiting, idle, paused and unavailable states.
Working models retain their coloured glow. Queued-only nodes have an amber dashed
outline and a slowly rotating dashed ring; their label says waiting. If a pool has
active and queued work, the active glow remains and an amber queue badge shows the
waiting work. Held uncertainty has a static dotted outline, never an active animation.
Reduced motion keeps the waiting label and outline but stops the ring rotation.

The GPT/Codex node shows "Waiting for bridge workers" while recorded Claude calls
are active or queued. This is a shared bridge activity indicator, not a claim about
this chat's private thinking or an acknowledgement that the parent has consumed a
result. A held Qwen job after a completed paid fallback does not keep GPT waiting.
Claude's waiting label uses an exact active parent and its queued or active
production Qwen child, matched by parent call ID, task and work item in the same
read-only snapshot. Completed, fallback, unrelated and router-shadow jobs never
produce that label. When all active Claude calls await a local result, the group
uses the waiting ring; mixed groups retain the working glow and show an amber
waiting badge and per-worker label. Runtime admission counts remain unchanged.
