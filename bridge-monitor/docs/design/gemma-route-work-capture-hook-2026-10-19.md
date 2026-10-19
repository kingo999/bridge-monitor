# Draft Gemma route_work capture hook design

Status: revised draft for review, 19 October 2026. This proposal authorizes no implementation or operational change.

Add an optional observer to the first submission of a registered router case. The observer freezes the request before paid execution and hands it to a bounded background capture worker. Gemma continues to recommend only; the existing router, admission checks, budgets, dispatchers and paid answer retain all authority.

**Suggested execution model: Claude Opus.** Implementing this proposal would add an observer at a live trust boundary, with concurrency, failure isolation and evidence timing to preserve. Advisory only; it does not switch the user's model or grant execution authority.

## Current behavior and design constraints

Source baseline: commit `51b24803597b49ab89dcd1b61982d323a4a03030`. The read-only starting checkpoint confirms active policy `43a200ea7bf9f73aad0cd5f642e5b51230ffd405f275342a81bc911b906770b2`, migration 007, 6,975 events and no busy jobs. Gemma is stopped. These describe the reviewed starting state, not future rollout prerequisites assumed to remain true.

`BridgeCoordinator.route_work` handles existing parent decisions first. For a fresh submission it loads a registered case, collects router facts, decides eligibility, captures the paid request, publishes the production decision and delegates paid execution. The classifier currently has no connection to this path.

The existing `capture_text` function is synchronous. It can probe capacity, connect to PostgreSQL and wait for a capture advisory lock. Wrapping it in `try/except` would isolate exceptions but would not isolate latency. The hook therefore must not call it directly on the request thread.

Keep three independent records: the raw recommendation, its deterministic would-be-allowed verdict, and the production parent-router decision. A blocked recommendation remains the original recommendation. A policy verdict or route disagreement does not establish reference-label correctness, successful execution or savings.

## Approaches considered

| Approach | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Bounded in-memory handoff to one capture thread | No capture database or model I/O on the request thread; small change to process lifecycle | Pending envelopes can be lost on exit; shares CPU and memory with the parent | Recommended for v1 |
| Direct synchronous capture with exception handling | Least new machinery | Database connection, lock and statement waits delay routing | Reject |
| Separate capture process with a durable handoff | Stronger process isolation and potentially durable delivery | Adds IPC, storage and lifecycle failure modes; durable submission can itself delay requests | Defer |

Manual capture remains the baseline and rollback operating mode. V1 is best-effort observational capture, not an audit of every incoming request. Shared-process contention is still possible; zero added latency is not promised.

## Proposed insertion point and scope

Inject an optional capture observer when constructing the **parent** coordinator. Its default is absent. Child coordinators and the Qwen shadow worker receive no observer. The coordinator depends on a small observer interface; classifier implementation imports belong in the parent bootstrap adapter, not in routing or admission modules.

Place one exception-isolated offer in the fresh-submission branch, after the existing `load_case` attempt and before router fact collection, decision publication or paid delegation. Leave the entire existing-decision recovery branch untouched. The MCP tool's input schema, output schema and response contents stay identical. The observer's return value must not enter routing expressions or the response.

V1 accepts only a case already loaded by the existing digest and baseline-validation checks, with matching `task_id`, `case.task_text == prompt`, output contract, approved capture scope and the registered single-file category. It captures cases regardless of Qwen availability, router sampling or eventual admission outcome; do not filter on `decision.eligible`. A missing, mismatched or unusable case produces a diagnostic skip, not a new router rejection. The observer never reads a repository via `source_id` or loads another case file.

This deliberately excludes unregistered research, general conversations and other requests without a trusted case origin. It measures the registered-case population, not overall router accuracy. Preserve the case's `natural` or `controlled` origin; never infer `natural` from caller prose.

Existing-decision retries create no new observation, including after a capture failure. Concurrent first submissions may offer identical or conflicting envelopes before either publishes a decision. Deduplicate only identical submission bindings; retain conflicting captures separately and never assume that both acquired an authoritative decision. Do not backfill a missing observation from a completed paid answer.

## Snapshot and trusted facts

Freeze an envelope containing task/work-item IDs, the expected production decision and paid-call IDs already computed by `route_work`, request text, registered baseline source, case digest, output contract and origin, capture timestamp, reviewed classifier configuration identity and the bounded trusted fields below. Exclude test vectors, expected outputs, answer bodies, model reasoning, tokens, credentials and arbitrary task context. Keep strings immutable and copy only selected bounded metadata; do not pass a mutable `TaskPacket`, `RouterCase`, coordinator or database connection to the worker.

Use the hook timestamp for `captured_at` and capture-time facts. Record persistence time separately in existing `facts_provenance` JSON. Queue delay must include the in-memory delay. Reject a declared `solution_available_at` at or before the hook timestamp. In the background, check only result metadata for a delivered result on the work item at or before that timestamp; if one exists, omit the envelope from new pre-solution capture. A later paid completion must not disqualify a correctly frozen envelope. This establishes timing within recorded work-item history, not proof that no solution existed elsewhere.

| Fact | Proposed source |
| --- | --- |
| `general_available` | `false`, from the reviewed deployment assertion that no general local worker exists |
| `code_available` | `null` in v1; the current route snapshot does not establish free, non-uncertain capacity |
| `paid_allowed`, `tools_available` | Explicit reviewed classifier configuration assertions, with configuration hash and load time; unknown if not asserted |
| `tests_present`, `context_complete`, `scope` | Matching validated registration: trusted test presence, supplied baseline and single-file contract |
| Security sensitivity and external tool requirements | Unknown unless separately supplied by a reviewed host fact; never parsed from request text |

For the hook adapter, a permissive model default is not an explicit operator assertion: track which fact fields were supplied in the selected TOML. This does not change the existing manual-capture defaults. The background worker owns a private configuration snapshot; later mutation of a caller or config object cannot change an accepted envelope's facts, profile or identity.

Do not call `configured_facts(dsn=...)` later and describe its capacity probe as capture-time availability. Pass the frozen facts explicitly into `capture_text`. The advisory `paid_allowed` assertion is not live admission or budget permission. Unknown code availability will block `code_local` with `code_worker_unknown`; report that fact coverage when comparing hook captures with earlier manual captures. It is not by itself a wrong raw label.

In the worker, compute a versioned binding hash from a canonical encoding of task/work-item IDs, expected decision/call IDs, request/source hashes, case digest and output contract. Record these fields under `facts_provenance.route_work_binding`, alongside `capture_method = route_work`, and use `origin_ref = route_work:<work_item_id>:<binding_sha256>`. The case digest distinguishes different registrations even when their task text and baseline match. Keep the existing source-key algorithm, profile, rubric, model digest and replay identity rules; only the hook's origin-reference convention changes. Automatic capture has no replay ID. Manual captures retain their existing identities and can overlap an automatic capture; distinguish capture methods and unique work items when reporting counts.

## Configuration and bounded handoff

Propose a new `[route_work_capture]` section in the explicitly selected local classifier TOML. `enabled = false` by default. Both this switch and `classifier_shadow.enabled` must be true. The existing enabled manual-capture config must not silently turn on the hook after an upgrade. No setting moves into live policy in this proposal.

Load the opt-in configuration during parent bootstrap, outside request handling. Resolve its path inside the project. Missing or invalid optional configuration, import failure or capture-worker startup failure leaves the observer absent and emits only a bounded diagnostic on stderr. It must not prevent the parent MCP server from starting. Do not silently select the local probe config or copy an example into operation.

Proposed initial bounds are eight pending envelopes, one active persistence operation and 24,576 combined request/source characters per envelope. The character check is a cheap admission bound; the worker still enforces the existing exact UTF-8/JSON byte limit. The two strings retain at most roughly 96 KiB per envelope, excluding object and processing overhead. Bound each copied identifier or original origin reference to 256 characters and the worker's serialized provenance to 4 KiB. Reject excess input or metadata without truncating or silently changing it.

The hot path checks the cached enable state and bounded metadata, then performs one non-waiting queue offer inside `try/except Exception`. It performs no database, file, network, serialization, hashing, model or worker-join operation. Queue exhaustion or submission contention drops the envelope. Do not create a thread or executor future per request. Ordinary exception isolation must not swallow process termination or cancellation signals indiscriminately.

The one background worker validates and serializes the envelope, computes hashes and calls the existing ingest/store layer with explicit frozen facts. Use a dedicated connection and transaction, independent of production admission. Add an optional bounded database-operation path: proposed connection timeout 1 second, lock timeout 50 ms and statement timeout 250 ms. Keep these per connection/transaction. Existing store calls require adaptation to honor these bounds; they do not already provide them. They bound ordinary waits, not every possible OS or network stall.

Expire an envelope older than the existing 600-second queue-age limit before persistence. For persisted observations, retain the existing database queue bounds and expiry behavior, measured from the original hook timestamp. For automatic capture, memory overflow, exact byte overflow and a full database queue are drops with process counters; they create neither an observation nor content objects. The hook therefore needs a distinct bounded store option. The existing manual path remains unchanged: its `queue_full` skipped rows can retain request/source bodies, so calling it unchanged would defeat automatic backpressure.

### Durable capture budgets

The pending queue bounds work awaiting classification, not total storage or write volume. Give automatic capture a database-wide budget, independent of model availability and process lifetime. Proposed initial hard caps are 256 persisted hook observations and 4 MiB of cumulative serialized `ClassifierInput` bytes, plus one successful persistence per two seconds. The byte charge includes the request/source encoding and facts; the row cap and bounded provenance separately limit metadata. These are logical capture limits, not an exact bound on PostgreSQL index, WAL or backup bytes.

Propose a singleton `classifier_hook_capture_budget` row for the fixed `route_work-v1` budget scope. It holds consumed observation/byte counts and the database time of the last successful persistence. Under the existing classifier capture advisory lock, check deduplication, queue capacity and the remaining budget before writing content; insert the observation/content and charge the budget in the same transaction. A duplicate consumes no additional budget. A rejected envelope writes no skipped row or rejection receipt to the database. All parent processes share this accounting, including concurrent submissions and ambiguous commits. An accounting error disables the sink rather than admitting an uncharged write.

Apply the two-second cooldown before background database attempts within each process, dropping rather than accumulating work during cooldown; enforce the successful-write interval again using the shared budget row. Exhaustion latches that process's sink off without polling. A restart, changed profile or configuration reload must not reset the database counters or choose a new budget scope. Raising caps or replenishing the budget requires a separate operator-approved change; automatic replenishment and retention deletion are outside v1. Local counters record `database_queue_full`, `rate_limited` and `capture_budget_exhausted` losses. No counter or diagnostic is written through the production emitter.

After a database error or ambiguous commit, disable that process's capture sink and discard its pending envelopes with a reason count. Do not retry an uncertain persistence operation or spawn replacement workers. The authoritative request continues. A parent restart starts with no surviving in-memory queue. Graceful shutdown stops offers, discards pending envelopes and permits only a bounded join, proposed at 250 ms; an active capture thread must not keep MCP shutdown alive indefinitely.

## Late actual-route links

The runner currently reads the production decision only when finishing classification. A fast classification can precede decision publication, leaving the link unknown indefinitely. A `work_item_id` alone establishes neither timing nor which concurrent submission acquired that decision.

### Exact submission binding

Use the same binding validator for immediate and reconciled hook links. Look up the frozen expected decision ID, requiring the expected paid-call ID, work-item ID, `decision_stage = parent_router` and `execution_mode = production`. Verify the task association through the existing call/work-item records. Compare the decision's `gate_facts.task_sha256`, `baseline_sha256`, `verifier_ref` and `output_contract` with the captured request/source hashes, case digest and output contract. Never select an arbitrary earliest or latest work-item decision, or search for a different mapped decision when the expected one is absent or mismatched.

Only a fully matching decision with a concrete mapped route (`paid` or `local` to `code_local`) may fill the link. Record the decision ID, binding version/hash, checked identity fields and observation time as route provenance, atomically with the route. Missing decisions, unsupported mappings, conflicting submissions and incomplete bindings remain NULL with distinct bounded diagnostics. Historical captures without the frozen binding are not automatically enrolled in this hook reconciliation; their existing evidence remains intact.

### Isolate lookup from classification completion

The current `runner._fields` performs an unguarded `actual_route_for` lookup before `store.finish`. A lookup error after inference can therefore discard a returned response and leave the observation dispatching. Make immediate route enrichment independently bounded and optional for every capture method, not just for the new reconciliation step. Use a separate connection/transaction with the short lookup limits; on an ordinary lookup error, close or roll back that transaction, leave the route NULL and proceed to save the raw response, interpretation, verdict and usage normally. Do not set the classification's error or uncertain state solely because route enrichment failed, and never resend the model request to obtain a route link. Manual captures keep their existing matching semantics; hook captures require the exact binding above.

This isolates a failed optional lookup from a healthy outcome write. A failure of the required `store.finish` transaction still follows existing uncertain/no-resend handling; it cannot be promised away during a database outage. Termination and cancellation signals retain their existing handling.

### Reconciliation with bounded scanning and saved progress

Include reconciliation in the existing manually invoked runner, before endpoint availability checks. Each pass examines at most 64 completed hook observations with a work-item ID and `actual_route IS NULL`, using an ordered index page before looking up decisions. Missing or mismatched decisions consume a place in that page and advance the scan. Limiting only the number of resolvable matches would allow an expanding unresolved history to be scanned repeatedly until timeout.

Propose a partial index on `(captured_at, observation_id)` for that candidate population, plus a singleton `classifier_route_link_progress` row keyed by binding/reconciliation version. Persist the last examined key and a fixed upper key for the current sweep. Advance by key rather than OFFSET; select the sweep's upper key through the same index. Read one page, validate decisions in a batch, conditionally fill matching links and advance the cursor in one short transaction under the existing single-runner lock. Use `actual_route IS NULL` and the expected immutable binding in the fill condition. Commit route, provenance and observation time together; a concurrent fill must never overwrite another value.

Save progress even when every decision in a page is missing, unmapped or mismatched. At the upper key, finish that sweep and start a new sweep on a later pass, allowing earlier unresolved rows and captures completed behind the cursor to be revisited. New arrivals do not move the current sweep's upper key. The cursor survives manual runner restarts, including single-pass invocations with Gemma off; it advances over examined candidates, not just successful links. Thus a healthy database permits eventual visits through a finite backlog across enough passes, without promising that one invocation reconciles everything.

Use the one-second connection, 50 ms lock and 250 ms statement limits, a fixed number of batched statements and no per-observation connection or retry loop. A transaction failure rolls back both its fills and cursor movement, records a bounded diagnostic and yields to normal classification; do not retry that page within the same iteration. Repeated timeouts suspend reconciliation for that invocation and report stalled progress. Missing schema or corrupt progress state also disables enrichment for that invocation rather than restarting an unbounded scan. Verify the indexed access and progress on a large representative backlog before enablement; a 64-row limit alone does not establish bounded query cost or an OS-level wall-clock guarantee.

For new records, leave missing or unmapped routes as SQL NULL rather than writing the literal `unknown`: migration 007 permits only an empty actual-route field to be filled later. The API can continue presenting an unresolved value as unknown. Do not rewrite existing non-NULL values, including historical `unknown`; corrections require separate append-only evidence. Never modify raw output, recommendation, verdict, usage or evaluation rows during reconciliation.

If the runner is stopped, late links remain unknown until its next manual invocation. With Gemma off, a runner invocation may reconcile links and expire stale inputs while still claiming and sending no model work. Here “actual route” retains the existing meaning of a recorded production parent-router decision; it is not proof of successful admission, completion or delivery.

## Failure reporting and boundaries

Keep fixed-name process counters for offers, accepted envelopes, memory/database queue drops, size drops, binding skips/mismatches, expired envelopes, rate/budget drops, persistence outcomes, uncertain persistence, disabled sinks and reconciliation progress/timeouts. Use bounded, rate-limited stderr diagnostics containing reason codes, versions and safe identifiers; never print prompts, source, facts containing free text, DSNs or tokens. Diagnostics run off the request thread. Do not use stdout, the production event emitter or production usage tables. Counters lost on process exit cannot establish a durable coverage denominator; the durable budget and cursor are safety/progress accounting, not a record of every rejected offer.

Raw request/source/response bytes continue through protected `content_objects`; only classifier tables and classifier-owned content objects may be written. A capture database failure must never mark the authoritative emitter degraded or prevent paid admission. No classifier observation, reference label, policy verdict or route link is returned to the paid worker or parent as an authoritative instruction.

The hook starts neither Gemma nor the classifier runner. A scheduled task, runner supervision, Skynet fallback, model promotion, policy enable switch and any route-selection capability are outside this proposal. Keep the pinned model/profile/rubric and existing single-flight, no-resend and GPU-residency checks.

## Verification required before implementation can be enabled

Use deterministic paid/local test doubles and the dedicated test database. Make no paid model calls to test this change.

1. Missing, false, invalid and inherited manual-only configurations leave the hook off. Disabled request handling performs no classifier I/O or model access. Parent startup survives optional setup failure; child and shadow-worker audiences never create a sink.
2. A persistence worker deliberately blocked on a test barrier does not prevent `route_work` from returning. Verify bounded queue/memory behavior and drops under pressure. Measure hot-path latency separately; do not claim zero overhead or reuse model-latency history as a threshold.
3. In paired enabled/disabled runs, deterministic fixtures return the same response and authoritative request bytes, decision, admissions, reservations, calls, jobs, fallback and delivery behavior. Inject offer exceptions, full queues, database connection/lock/statement failures, worker failure and missing classifier schema. Permit only classifier tables and classifier-owned content differences.
4. Check matching and mismatching task/case/contract/scope, hostile request text, missing registrations, declared earlier solutions and earlier delivered-result metadata. Freeze inputs before a paid test double mutates caller objects. Never capture answer bodies or hidden test vectors. Preserve natural/controlled labels and frozen fact provenance.
5. Check recovery retries, identical and conflicting concurrent first offers, submission-binding/source-key separation, overlapping manual captures, original timestamps, memory expiry, database queue expiry, ambiguous commits and shutdown. Same work-item IDs with different prompts, tasks or case digests must neither collapse distinct bindings nor inherit each other's route evidence. Pending in-memory loss must remain visible as a coverage limit.
6. Keep Gemma off and fill the database queue, then submit sustained automatic traffic across multiple parent processes and restarts. Assert that queue/size/rate/budget drops add no observation/content rows, cap boundaries are atomic, duplicates are not charged twice and ambiguous commits cannot bypass accounting. Exhaust the row and byte caps independently; verify that restarts/profile changes cannot replenish them and a disabled sink stops database attempts. Measure logical bytes and actual database/WAL growth separately. Preserve the manual skipped-row behavior.
7. Exercise classification before and after production-decision publication, reconciliation with Gemma off, conflicting fills, missing/unmapped decisions, incomplete historical bindings and immutable historical values. Check every expected ID, request/source hash, case reference and contract mismatch in both link paths. Assert that no other work-item decision is substituted, fills include matching provenance atomically, no model send occurs during reconciliation and no recommendation/verdict is rewritten.
8. After a fake model returns a valid response, fail or time out only the immediate route lookup while leaving `store.finish` healthy. Assert completed classification, preserved raw output/verdict/usage, a NULL route, later successful reconciliation and exactly one model send. Separately fail `store.finish` and retain existing uncertain/no-resend behavior. Apply the lookup-isolation checks to manual captures too.
9. Put many unresolved or mismatched observations before a resolvable one, run only one reconciliation page per invocation and restart between passes. Require eventual visitation and linking, bounded candidates/statements per pass and indexed plans on the representative backlog. Test an all-unresolved sweep, wraparound, new/backdated completions, concurrent fills, timeout rollback of cursor and fills, stalled-progress reporting, and absent/corrupt progress schema. No failed enrichment pass may prevent an otherwise healthy classifier result from being saved.
10. Retain classifier-to-dispatch import prohibitions and structural protection for eligibility, admission, scheduler and shadow worker. If the MCP file's existing blanket text prohibition needs adjustment, replace only that assertion with a narrow observer-boundary check plus the fault tests above; do not remove isolation tests wholesale. Confirm production metrics, Qwen cohort and operator-only content access remain separate. Schema compatibility failures disable only the optional hook/enrichment paths.

Likely implementation surfaces are parent bootstrap and coordinator lifecycle, one injected offer in `route_work`, a new capture adapter, classifier config/ingest/store budget and timeout support, shared route-binding validation, runner completion/reconciliation, focused tests and operations documentation. Binding metadata fits existing immutable provenance JSON. Durable budget accounting and resumable indexed reconciliation require a proposed additive classifier-only migration for the two singleton tables and partial index described above; keep migration 007 and production tables unchanged. Missing prerequisites leave the hook off and optional enrichment unavailable while preserving existing manual operation. These are review surfaces, not an implementation plan or authorization to create or apply a migration.

## Rollout and rollback for a separately approved implementation

Review this design and the eventual implementation before enabling capture. The proposed migration, parent MCP restarts and local enablement each require explicit operator approval. Deploy with the new hook switch false; validate migration locking/index behavior and missing-schema isolation in the dedicated test database before any approved live schema change. Validate disabled behavior, then use a small approved controlled case before collecting real registered cases at arrival. Start Gemma only for an intentional manual run and stop it afterward when appropriate. No live policy activation is needed or authorized by this design.

Configuration is a startup snapshot in v1. Turning the hook switch off requires an approved parent MCP restart to remove the running sink; editing the file alone does not stop it. Stopping the manual classifier runner prevents subsequent model sends but does not stop hook capture or cancel an already-sent request. Preserve captured evidence.

Rollback removes the injected offer and optional bootstrap/lifecycle wiring, followed by an approved parent MCP restart. Leave classifier tables, content, budget consumption, reconciliation progress and existing observations intact; no schema rollback or policy change is needed. Never reset budgets as part of rollback or a later restart. Keep manual capture available. Reconcile the older policy named in `parent-router-shadow.md` separately rather than silently changing it here.

The proposed design scope is the bounded, default-off registered-case observer, frozen facts with unknown code availability, durable capture budgets, exact submission binding, isolated immediate lookup and resumable late-route reconciliation, including their classifier-only schema prerequisites. It does not include implementation, migration creation/application, activation, service restarts, scheduling or routing authority.

## Source references

- [Existing classifier design](gemma-classifier-shadow-2026-10-19.md) and [operations](../operations/gemma-classifier-shadow.md)
- [Coordinator and parent MCP entry point](../../bridge/mcp_server.py), [bootstrap](../../bridge/startup.py), [case loading](../../bridge/router/cases.py) and [eligibility](../../bridge/router/eligibility.py)
- [Capture](../../bridge/classifier/ingest.py), [store and identity](../../bridge/classifier/store.py), [runner](../../bridge/classifier/runner.py), [facts policy](../../bridge/classifier/policy.py) and [contracts](../../contracts/classifier.py)
- [Migration 007 guards](../../database/migrations/007_classifier_shadow.sql) and [existing isolation tests](../../tests/classifier/test_shadow_runner.py)
