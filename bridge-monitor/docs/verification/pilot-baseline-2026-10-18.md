# Observed pilot baseline — 18 October 2026

This is a data-only checkpoint for later optimization review. The `phase1-v1`
observed cohort was queried at `2026-10-17T22:01:55.755107Z` (00:01:55
Prague time on 18 October). No model choice, route, gate or stored disposition
was changed.

| Measure | Observed cohort |
| --- | ---: |
| Work items | 13 |
| Dispatched to Skynet | 8/13 (61.5%) |
| Explicitly not routed | 3/13 (23.1%) |
| Residual route unknown | 2/13 (15.4%) |
| Resolved without a delegation record, within route unknown | 2/13 |
| Qwen results delivered to Claude | 7 |
| Claude dispositions for delivered Qwen results | 6/7 |
| Claude-reported accepted | 5/7 (71.4%) |
| Claude-reported accepted or revised | 6/7 (85.7%) |
| Paid takeover after Skynet dispatch | 1/8 (12.5%) |

The active agent policy `445c3debabdc63c8` accounts for seven work items.
Each has one Qwen result delivered to Claude. Five resolved as
`local_retained`, one as `paid_revision_with_contribution`, and one remains
unresolved because its Claude completion was malformed and lacked a
disposition. The six resolved work items took a median 20.757 seconds from
creation to recorded resolution (range 14.510–26.361 seconds). This small,
selected sample is not a throughput or latency forecast. The earlier
one-second Qwen timeout contributed the single historical paid takeover.

For all seven active-policy executions, the requested Claude profile `sonnet`
reported `claude-sonnet-5-5`; the requested and reported local model was
`qwen2.5-coder:14b-instruct-q4_K_M`. Stored usage observations total 498 input
and 198 output tokens for Qwen (`agent_ollama_terminal`), and 30 input and
3,661 output tokens for Claude (`claude_cli_result`). These are provenance-specific
reported fields, not a complete comparable billing basis: the Claude CLI input
total is too small to represent the full prompt and context. No cost or savings
estimate is made from them. The [disposition quality audit](disposition-quality-audit-2026-10-17.md)
found two of five `accepted` records that appear to meet the approved
`revised` definition. The recorded numerator remains unchanged for honest
telemetry, and needs independent review before quality conclusions.

## Backup confidentiality check

The retained prompts and responses are present in database dumps. The backup
directory and its then-existing children were ACL-restricted to the operator,
SYSTEM and Administrators. A post-change audit checked 17 targets, found zero
allow entries outside those identities, and confirmed protected inheritance
on the root. The backup command now refuses a broad output-directory ACL
before invoking `pg_dump`; a regression test first reproduced the former
failure and then passed against the guard. A real restricted backup completed
at `2026-10-17T22:00:55Z`:

- [Custom-format dump](../../var/backups/bridge-monitor-20261017T220055Z.dump):
  475,554 bytes, SHA-256
  `F55B1B35D2F687F0EB75746A8C34A7D35392D07FF061B108D3F7DC8D2CB075D4`.
- [Hash receipt](../../var/backups/bridge-monitor-20261017T220055Z.dump.json):
  hash and byte length matched the file; `pg_restore --list` read its archive
  catalog. Dump and receipt inherited only the three allowed identities.

The five PowerShell operational tests passed. The Python suite reported 83
passed and 160 skipped without a dedicated test database or live-model flag;
its one dependency deprecation warning is unrelated. An initial sandboxed run
could not terminate a child process, but that case and the full suite passed
with local process-control permission. This check verifies archive readability
and integrity. The exact dump was then restored into a uniquely named temporary
database in the project-local PostgreSQL cluster. The restored copy held four
schema migrations, 1,366 events, 1,061 content objects, 13 work items and 21
runtime jobs; the temporary database was removed after the check. The
[runbook](../operations/runbook.md) retains the retention caveat for any later
operator-accessible restore.

## Follow-up: Claude cache-token capture

A retained Claude CLI result frame demonstrated the measurement gap: its
`usage` had four ordinary input tokens and 2,440 cache-read input tokens.
The bridge previously kept only ordinary input and output. Future
`usage.reported` events now retain separate `cache_read_input_tokens` and
`cache_creation_input_tokens`, and migration `005_usage_cache_tokens` adds
nullable columns to `usage_observations`. The adapter, validation and
projection tests failed for the missing behavior before the change and passed
after it. The full disposable-database suite passed **243 tests**, with two
skips and one dependency warning.

Before the live migration, the restricted backup
`bridge-monitor-20261017T221054Z.dump` had SHA-256
`8D5287596624F609BAEA273E356AAF4DAEBD3C60067BA72052C564F5A9689355`.
The live database applied migration `005_usage_cache_tokens`; all 20 earlier
usage observations retain null values for both new fields. The dashboard
returned HTTP 200 after migration. At that point, the already-running desktop
MCP process still had its previously imported adapter code, so native capture
needed a process reload. No historical values were inferred or
rewritten, and no cost or savings estimate follows from the new counters
alone.

One fresh isolated [pilot receipt](../../var/probes/cache-usage-5c3e13173db3.receipt.json)
then exercised the updated process with active policy `445c3debabdc63c8`.
Work item `8ddb8025-856e-4068-9dd3-f1c0889d854d` followed one local route,
resolved as `local_retained`, and reported complete accounting. Its persisted
Claude usage row has 4 ordinary input, 1,745 cache-read input, 2,106
cache-creation input and 264 output tokens from `claude_cli_result`. The Qwen
row has 42 input and 3 output tokens from `agent_ollama_terminal`, with cache
fields null. Both usage events were retained; no slots or source locks remained
held. This proves live end-to-end capture in a freshly launched bridge process.

After the user restarted Codex, the desktop MCP process started at 00:14:25
Prague time, later than the adapter edit at 00:07:41. A native
`bridge_monitor.delegate_claude` call for work item
`e02ad7bf-0bea-4afa-b738-9b3ce9b02b8e` completed GPT → Sonnet → Qwen with
one actual Skynet dispatch, complete accounting, an `accepted` Claude
disposition and `local_retained` resolution. Its persisted Claude usage has
4 ordinary input, 2,815 cache-read input, 1,044 cache-creation input and 275
output tokens. Qwen reported 42 input and 3 output tokens; its cache fields
remain null. No runtime slot remained held. This verifies the ordinary desktop
path after reload. The disposition is model-reported, not an independent
quality judgment. The cohort table above remains the earlier dated snapshot.

## GPT acknowledgement and dashboard label check

At `2026-10-17T22:18:40Z`, the observed cohort contained 15 work items: 10
routed, three not routed, and two with no recorded routing decision. Nine Qwen
results had reached the Claude consumer; eight had Claude disposition records.
There were no GPT disposition obligations or acknowledgement records. The
dashboard therefore cannot establish whether GPT used the final Claude answer.
Zero GPT-reported acceptance is **unknown final use**, not a rejection or a
quality assessment. The approved acceptance numerator and denominator were
left unchanged.

The dashboard now labels the acceptance card and selected filter according to
the consumer stage. With `GPT / Codex` selected and no GPT acknowledgements, the
card explicitly says that final use is unknown. A browser regression test first
failed on the old Claude-specific label, then passed after the change. The full
browser suite passed nine tests; TypeScript typecheck and static build passed.
The project-owned dashboard process was stopped through its guarded script and
restarted by the scheduled watchdog at `2026-10-17T22:21:53Z`. The live page
returned HTTP 200 after restart. This is a presentation correction; it does not
add GPT acknowledgement capture or infer missing events.

A later [read-only code-reasoning diagnostic](code-reasoning-pilot-2026-10-18.md)
tested the routing arithmetic with one paid-direct call caused by an
undersized task call budget and one actual Sonnet → Qwen exchange. It found a
questionable rationale for a model-reported `revised` disposition. The
observations were retained without changing the stored judgment or gate.

## Inspector usage visibility

The exchange inspector now shows each stored model usage observation beside
its requested/reported model, including ordinary input, cache-read input,
cache-creation input, output, scope, cumulative flag and provenance. It
renders a missing counter as `Not reported`, distinct from a recorded zero.
It does not sum cumulative and incremental observations or infer billing cost.
The detail API returns the metadata without embedding prompt or response
bodies; those remain separately fetched under the operator session.

A query regression test failed while the detail API omitted usage, then passed
with both complete and partial observation rows. A browser test likewise
failed when the usage panel was absent, then passed with positive, zero and
unknown cache values. The relevant Python integration suites passed 16 tests,
the full browser suite passed 10 tests, and TypeScript typecheck and static
Next.js build passed. The guarded dashboard stop triggered the scheduled
restart. After restart, the live page, operator login and detail request all
returned HTTP 200. Work item `65ff75dd-f7f4-438f-aafa-435e6825460c`
returned both usage rows: Qwen cache-read input unknown and Sonnet cache-read
input 6,973, matching the persisted records. No model execution or routing
setting was changed for this display update. A final browser assertion caught
that the usage cards showed only one model name; they now label requested and
reported models separately. That focused test, TypeScript typecheck and the
static build passed after the label change. The scheduled dashboard restarted
again and returned HTTP 200 with the rebuilt asset.

## Prompt-chain inspector check

The detail API already retained both GPT → Claude and Claude → Skynet messages,
but the Exchange tab had selected the Skynet child as its single “Original
prompt.” The inspector now shows the original GPT request, the effective prompt
sent to Claude, and, when routed, the original and effective Skynet subtask.
Claude's captured worker instructions are expandable. No event schema or
content storage changed; every body still loads separately through the
operator-only content endpoint.

A browser test ingested a linked two-call fixture with distinct original and
effective bodies. It failed on the missing GPT card before the UI change and
passed afterward. The first full browser run exposed fixture interference with
the 100-item synthetic metric test; moving this fixture into the imported
cohort resolved it. The full browser suite then passed **11 tests**, and
TypeScript typecheck and the static build passed. The first build attempt hit
`EPERM` while creating one export directory. ACL inspection found the project
directory writable; manually creating that target subdirectory succeeded, and
the next build completed. The cause of the initial `EPERM` was not established.
After the guarded dashboard restart, the live page and authenticated detail
returned HTTP 200.
The recent observed work item `65ff75dd-f7f4-438f-aafa-435e6825460c`
exposed three parent messages and two Skynet child messages through the live
API. Existing bridge sources and the active route were unchanged.

The same live work item had 819 stored Qwen and 164 stored Claude
`response.delta` events. At this point the timeline displayed the first 500
events and marked truncation; the exact later deltas remained in PostgreSQL.
This prompt-card change does not add a live text preview or change the
selected-item refresh rate. Those are separate dashboard optimizations to
measure before expanding traffic.

## Long exchange timeline check

The first-500-event window hid the final activity of long exchanges. The
detail query now returns the first 100 and latest 400 events, in time order,
when more than 500 exist. The timeline marks the omitted middle. Event
retention, payloads, and routing are unchanged.

A regression with 700 additional deltas failed before the query change because
the last delta was absent, then passed. A browser regression failed before the
timeline gap marker was added, then passed after the static rebuild. The full
Python suite passed **245 tests** with two skips and one dependency deprecation
warning. One full browser run had a first-load timeout at “Checking operator
session…”; a fresh run passed **all 12 tests**. TypeScript typecheck and the
static Next.js build passed.

The guarded dashboard restart succeeded. The live page, operator login, and
observed detail returned HTTP 200. Work item
`65ff75dd-f7f4-438f-aafa-435e6825460c` had 1,022 stored events; its API
returned 500 with truncation marked. The API's last event was the same
`call.completed` event returned by PostgreSQL's latest-event query. The
middle events remain available in PostgreSQL for later analysis.

## Selected live-output preview

The Exchange inspector now requests an operator-only output preview for each
selected call without a delivered result. It shows the latest captured text
while Qwen or Claude is still working, then relies on the retained full result
body once one is delivered. The preview reads the latest response message's
projected chunks in `chunk_sequence` order. It is bounded by
`preview_max_chunks` and `preview_max_bytes` in the dashboard service config;
earlier exact bytes remain in PostgreSQL. The timeline's head/tail counts are
also configurable as `timeline_head_events` and `timeline_tail_events` and are
reported to the UI as the gap position. A service restart picks up these
values. Both the HTTP route and the body-returning query enforce operator
access. No model, gate, disposition, or retention policy changed.

An operator-only query test first failed because the preview route did not
exist. A browser test then failed because the active Qwen preview card was
absent; after implementation it showed two chunks and updated when a third
arrived. Reversed event timestamps exposed another defect: the first query
assembled `Gammaβ Alpha` instead of `Alpha β Gamma`. Reading projected chunks
by sequence fixed it. The final Python suite passed **246 tests**, with two
skips and one dependency deprecation warning. The final browser suite passed
**13 tests**. TypeScript typecheck and static export passed. One earlier
export attempt hit `EPERM` while Next created a generated `_not-found`
directory; creating that exact project-local directory let the next build
succeed. The cause of that filesystem error remains unknown.

The guarded restart loaded the updated service. The live page, operator login,
and selected detail returned HTTP 200; an anonymous preview returned 401. The
Qwen preview returned 80 chunks with truncation marked, and its text matched
the same sequence-ordered PostgreSQL chunks without printing retained content
in the verification output. A separate burst probe found two selected-detail
requests for 20 events, so no refresh-throttle change was made.

## Paused selected exchange

The dashboard's pause control now holds selected-exchange refreshes as well as
flow and metric refreshes. It also freezes the inspector's elapsed-output
counter. It records that the selected item changed while paused and fetches
its latest detail once display resumes; the counter then resumes from current
time. A replay reset also marks the selected detail for refresh. Collection
and PostgreSQL persistence continue during pause; routing and model policy are
unchanged.

The browser regression first failed because a newly committed Skynet status
appeared in the selected inspector while display was paused. It failed again
when the elapsed-output counter continued to tick. After the changes, both
values stayed fixed until resume. TypeScript typecheck and the static Next.js
build passed, followed by all **14 browser tests** against a disposable
PostgreSQL database. The Python suite was not rerun because this change is
limited to the dashboard client and browser test.

The guarded restart completed. Its first HTTP check returned 200 before the
watchdog wrote the new process receipt; a second check verified the recorded
PID/start-time identity and HTTP 200. No tracked file in the existing bridge
source directory changed.

A long-output browser check also scrolled within an expanded Qwen preview,
committed another captured chunk, and confirmed that the reader's scroll
position stayed fixed. It passed without another product change. The complete
browser suite passed again: **14 tests** against a disposable database.

## Expanded work-list continuity

After an operator used **Load more**, each SSE-triggered dashboard refresh
previously replaced the expanded work list with its first 100 rows. A browser
regression reproduced this by loading a 101st synthetic work item, committing
another, and observing that the list fell back to 100 rows. The dashboard now
remembers the oldest explicitly loaded work item and refreshes 100-row pages
through that item. A filter change starts a fresh list; the live topology and
underlying event storage are unchanged. The same regression now shows the new
item while retaining the older row. TypeScript typecheck, static build and all
**15 browser tests** passed against a disposable PostgreSQL database. No
Python runtime or routing code changed in this follow-up.

The guarded dashboard restart loaded the new static build. The watchdog's
replacement process matched its recorded PID and start time, and the live page
returned HTTP 200. The tracked existing bridge sources still had no diff.
Refreshing a very large manually expanded list now reads every loaded page;
the measured large-list query cost is recorded below.

## Expanded-list query measurement

A disposable synthetic database supplied 100, 500 and 1,000 work items to the
same 100-row history query used by the dashboard. After one warm-up, five
sequential refresh samples had medians of **68.81 ms** for one page,
**449.54 ms** for five pages and **1,620.42 ms** for ten pages. The aggregate
[timing receipt](../../var/reports/dashboard/pagination-benchmark-2026-10-18.json)
retains every sample. In a second disposable database with 1,000 rows, an
in-memory diagnostic relaxed the query's 100-row guard solely for comparison:
ten production-sized pages had a **1,220.99 ms** median, versus **186.83 ms**
for one 1,000-row query, with identical ordered work-item IDs. The
[comparison receipt](../../var/reports/dashboard/pagination-query-comparison-2026-10-18.json)
retains those samples. No project query limit or live API was changed.

These are local database query times, not browser refresh latency or a service
SLA. The observed pilot cohort at the earlier checkpoint had 13 work items;
the large-list path is not yet representative of its normal load. The
configured bulk-read change below addresses repeated-query cost while keeping
a finite upper bound.

## Configured bulk history read

The authenticated work-list API now accepts a higher `limit` only up to
`work_item_max_limit` in the dashboard service TOML (1,000 in the example;
validated range 100–2,000). The operator session advertises that bound to the
browser. The default first page and ordinary **Load more** requests still use
100 rows. Once the operator has expanded the list, refresh asks for enough
rows to include the oldest loaded item in one bounded read when possible, then
uses 100-row fallback pages beyond the configured ceiling. A filter change
returns to the ordinary first page. The response contains work-item metadata,
not retained prompt or response bodies; content access remains separate and
operator-only.

An API regression first failed for the missing configured parameter, then
verified a 101-row authorized read, a 401 anonymous response, a 422 request
above the configured limit, and the bound advertised by the operator session.
The browser regression first failed because refresh still made only 100-row
requests; it now observes a bounded bulk request while preserving the older
item. The full Python suite passed **247 tests**, with two skips and one
Starlette dependency deprecation warning. TypeScript typecheck, static build
and all **15 browser tests** passed. No model choice, route, gate or retained
content changed.

The guarded restart loaded `work_item_max_limit=1000` from the active example
config. Its replacement process matched the PID/start-time receipt and served
the page with HTTP 200. The authenticated live session advertised 1,000; a
101-row history request returned HTTP 200 (17 observed rows currently exist),
and a request for 1,001 returned HTTP 422. No retained body was printed during
that check. In a disposable 1,000-row cohort, the implemented bulk query had
a **186.71 ms** median versus **1,230.77 ms** for ten 100-row queries across
four post-warm-up samples; both returned identical ordered IDs. The
[verified timing receipt](../../var/reports/dashboard/pagination-bulk-verified-2026-10-18.json)
contains the samples. These are query timings, not end-to-end browser latency.

## Stable live stream connection

The dashboard previously reopened its SSE connection whenever a refreshed
snapshot advanced the cursor. A browser regression committed three observed
work items and counted **four** stream requests while the rows appeared. The
stream effect now stays mounted while snapshots and list data refresh. Its
cursor ref still advances from publications, and the browser's EventSource
handles reconnection when the transport actually drops. The same regression
now receives all three items with **one** stream request.

The initial dashboard load had a related race: a work-list request could finish
before a new event while the concurrent snapshot finished after it. The stream
would then begin past that event, leaving the list stale until another event.
A browser regression held the snapshot response, committed a work item, and
reproduced the missing row. The dashboard now reads the snapshot first to
establish its cursor, then fetches metrics and work items. The stream can
replay anything committed after that cursor. An existing no-route row test also
waits for the initial live connection before posting its event batch.

TypeScript typecheck, static build, and all **17 browser tests** passed against
a disposable database. The static build needed an unsandboxed retry after a
sandboxed `mkdir` returned `EPERM`; no code change was needed for that build
failure. The guarded live dashboard restart produced a replacement process
whose PID and start time matched its receipt; HTTP returned 200. Model routing
and the existing bridge sources were not changed.

## Cohort switch consistency

Two browser regressions delayed requests while the operator switched from
Sample data back to Observed. The first reproduced an older metrics/list
refresh replacing the current cohort after its response arrived. The second
reproduced a delayed **Load more** page appending a Sample data row into the
Observed list. The dashboard now applies only the latest refresh result and
discards a pending page when the filter changes. This preserves the operator's
current cohort without changing the event stream or historical records.

The full browser suite uses one disposable database for all tests. The new
checks compare against the Observed cohort captured at each test's start;
the existing expanded-list test now counts its loaded rows instead of assuming
that no earlier test added synthetic work. TypeScript typecheck, static build,
and all **19 browser tests** passed. The guarded restart loaded the new build;
its replacement PID/start time matched the process receipt and HTTP returned
200. The existing bridge source and routing policy were not changed.

## Live dashboard demonstration

An authorized read-only desktop call created task
`309e174e-0588-461a-81b0-b36160d6753c`, work item
`7e77b80e-628c-439d-9a1f-06f51a2e4edd` and parent call
`cb270054-1c2d-4db3-902b-7776e0124751` under active policy
`445c3debabdc63c8`. The first submission included an unapproved
`source_id` and was rejected as `source_scope_not_approved`; no worker ran
for that submission. The normal desktop envelope then completed in about
23 seconds with requested `sonnet`, reported `claude-sonnet-5-5`, and a
Skynet dispatch to the pinned Qwen 2.5 Coder profile. The dashboard showed
the new Observed row, both prompts, the Qwen and Claude responses side by
side, a Claude-reported `accepted` disposition and `local_retained`
resolution. The answer was `37 × 14 = 518` with the same arithmetic check
retained by Claude.

The parent status was `completed`, delivered, with no held slot. Capture was
healthy with zero known gaps, pending spool events or pressure. GPT's
downstream disposition remains `Unknown`: the current parent completion path
only accepts Claude-stage dispositions and cannot create a valid GPT-stage
acknowledgement for this call. No GPT acceptance was invented, and no model
or routing policy was changed for the demonstration.
