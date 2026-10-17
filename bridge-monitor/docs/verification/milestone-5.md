# Milestone 5 review evidence — 17 October 2026

Status: **review-ready isolated pilot; no global cutover**. The recovery and
capture fault checks, configured retention, bounded history, operator path and
process guards, backup/restore, and a limited live read-only paid fallback are
verified below. The existing bridge sources and Codex client registration were
not changed. The live Skynet timeout remains an uncertain held reservation;
there is no claim of individual remote termination or a delivered local answer.

## Recovery and data integrity

The `tests/recovery` matrix uses a separate worker process that exits after a
launch claim, fallback/completion and cancellation races, an owner-termination
paid-recovery queue resumed after parent restart, and a collector outage with
overflow and duplicate replay. A stale heartbeat never releases a slot; a
correlated termination does. A live receipt cannot turn a committed local
fallback back into an active deliverable call. A late result stays retained but
undelivered after fallback or cancellation, with no invented disposition.
Queued paid recovery resumes from its durable link on parent MCP startup;
neither owner nor local inference is replayed. Capture recovery keeps event IDs
and exact Unicode bytes and creates one visible gap for overflow.
After read-only review, owner termination, child recovery admission and any
rejection are committed in one transaction. A simulated crash rolls them all
back, and the rejection is recorded in the reconciliation evidence and exposed
in the work-item inspector. A completed Qwen child still receives paid recovery
when its Claude owner dies before answering GPT. A simulated database
connection outage leaves no partial owner/recovery state and can be retried.
Recovery admission checks that the exact saved prompt bytes still exist; an
expired prompt produces `recovery_prompt_unavailable` rather than a doomed
queued paid call.

Retention uses the active policy's `retention_days`. Migration `004_retention`
records each run and replay boundary. Expired bodies become `removed` tombstones
with their original hash, length and identity; old publication entries expire,
and SSE cursor expiry still requires an explicit snapshot reset. Re-ingesting an
old event cannot restore removed bytes. Active-job prompts, unresolved
disposition results, approved example bodies and registered guidance bodies
are protected. A filtered history page scans 100 candidate work items per
batch and returns no embedded body bytes; a rare filter or high offset may
require many batches. The detail timeline exposes a truncation flag after 500
events rather than implying it is complete.

The [synthetic load receipt](../../var/reports/m5-synthetic-load.json) measured
100 events with 1,000-byte Unicode bodies: 126,976 bytes in the SQLite spool
at peak, 8,872.74 ms to enqueue during an outage and replay after reconnect,
and 53.42 ms for a 100-publication query page. The first event to completed
page read took 8,926.15 ms in this one test run. These are observations of a
small local workload, not a throughput or latency SLA. Adoption thresholds
should be set from a longer representative run.

## Operator checks

`start-monitor.ps1` and `stop-monitor.ps1` use project-owned paths and a
recorded server PID with original process start ticks. A Windows virtualenv
launcher created a distinct Python server child during the first test; the
initial stop guard left that child alive. It was identified by the exact
project command line and stopped. The corrected scripts record the serving
PID, and the retest verified both `/api/v1/session` health and closure of the
listener after stop. Outside-project config and backup paths were rejected.
The stop command leaves PostgreSQL and model execution state untouched.

The [pilot backup/restore receipt](../../var/reports/m5-pilot-backup-restore.json)
records a project-local custom-format dump and separate restored database.
Migration/task/call/result/content counts matched exactly: `4/3/7/3/118`.
The expanded source and restored counts also match for runtime jobs,
reconciliations, events, retention runs, decisions and dispositions; see the
receipt. Older backups can restore bytes later removed by retention, so the
runbook requires reapplying retention before restored data is exposed.
The [runbook](../operations/runbook.md) covers startup, policy checks, access
controls for full content, reconciliation, capture outage, retention, stop,
backup/restore and rollback. The [pilot checklist](../operations/pilot-checklist.md)
describes the controlled read-only sequence; it does not authorize broader
registration or routing changes.

## Limited live flow

The fresh project database `bridge_monitor_pilot_m5_20261017` used the
previously reviewed capacity policy version `77f6331062bd9666`, hash
`77f6331062bd9666e22bd354421240c676bfe07381883f525301459378903eb6`.
It pins `sonnet`, the Qwen profile
`qwen2.5-coder:14b-instruct-q4_K_M`, and its approved digest. One task was run
first, then two separate paid tasks concurrently. The older one-worker policy
file now validates to a different hash than its historical approval under the
current validator; it was **not** silently activated. Thus the first run had
one active paid worker under a reviewed two-slot limit, rather than an active
one-slot policy.

The [timeout pilot receipt](../../var/probes/m5-timeout-20261017.receipt.json)
records Codex → Sonnet and in-owner paid continuation. Separate pilot database
inspection showed the Skynet request and its held Qwen slot; the receipt alone
does not prove remote execution. Codex CLI exited zero; the bridge completed with accounting
complete; the fallback transition is `completed/execution`, and the work
resolution is `paid_replaced`. The raw decision history is `local, pending,
paid`; `pending` is a dispatch-recheck audit record, so the effective route
sequence is `local, paid`. The pilot harness initially returned exit 1 because
it included that audit record in an exact route assertion; the corrected
assertion passes when evaluated against the retained receipt. The harness was
not rerun against Skynet because the timed-out request still holds the only
Qwen slot. The local model was requested but no reported model or delivered
local answer was observed, so no Claude disposition was fabricated. Sonnet
reported `claude-sonnet-5-5` and delivered the correct arithmetic answer.
Its text claimed verification lines 9–100 without actually listing them, a
concrete response-quality finding preserved in the exact stored answer.

The [two-worker live receipt](../../var/reports/m5-two-claude-live-20261017T111552Z.json)
records two independent Sonnet calls from different roots, overlapping model
intervals, two dashboard worker components and Claude active capacity `2`.
Skynet held uncertain capacity remained `1`. Both paid calls completed. The
old pilot database's held Qwen reservation was not altered, and no existing
bridge or client registration was changed.

## Verification and limits

- Full Python suite: **197 passed, 2 skipped, 1 warning** in 162.19 seconds,
  with pytest temporary files confined to the project so the MCP path guard
  applies to the real handshake test.
  The skips are the opt-in normal Qwen pilot and a Windows symlink privilege
  case; the warning is Starlette TestClient's `httpx` deprecation.
- Dashboard TypeScript typecheck and static Next build passed. All **7
  Playwright tests passed** in 8.8 seconds using installed Chrome selected by
  `BRIDGE_MONITOR_PLAYWRIGHT_CHANNEL=chrome`; the package's default headless
  Chromium revision was absent from this machine's cache.
- The final enforced read-only Claude review found no proven exit blocker. It
  noted that the dashboard stop command does not stop a separate pilot MCP
  client; the runbook requires that client's work to drain or be cancelled
  separately. It also identified the expired recovery-prompt edge case fixed
  above. Its before/after source snapshots and independent file/Git comparison
  were unchanged. The reviewer did not run tests or inspect every receipt;
  the validation claims here are from the checks recorded in this project.
- The source-read pilot remains restricted. Write-source execution, a live
  process-loss/restart fault during a paid call, full 600-second client timeout,
  and a measured production-scale workload remain unobserved. The live pilot
  preceded the final recovery hardening, which is covered by PostgreSQL fault
  tests but has not been exercised again against live models. The active
  Qwen reservation requires correlated evidence before reuse. There is no
  learned gate, automatic policy update, or global cutover.

Specification acceptance criteria 1–6 and 8, 10–14 retain their passing
Milestone 1–4 evidence. Criterion 7 is extended by the process-loss and
queued-recovery restart matrix; criterion 9 by outage, retention and content
access tests; criteria 15–17 by fallback/cancellation races and the live paid
continuation. The documented limits above distinguish simulated fault paths
from live-observed adapter behavior.
