# Bridge Monitor Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if the user selects delegated execution. Execute task-by-task only after implementation is authorized. Checkboxes below describe future work; no implementation has started.

**Goal:** Deliver the agreed live bridge dashboard in five working milestones, retaining complete observable exchanges and accurate delegation, acceptance and paid-fallback accounting.

**Architecture:** A new Python bridge coordinates GPT/Codex, Claude workers and explicit Qwen delegation. A Python HTTP service persists events and operational state in PostgreSQL and serves query APIs and SSE to a client-rendered Next.js dashboard. All project files, local runtime state and development artifacts stay under `C:\AI\bridge-monitor`; existing bridges and registrations remain untouched.

**Tech Stack:** Proposed implementation choices are Python with Pydantic contracts, FastAPI, Psycopg 3 and pytest; PostgreSQL; Next.js with TypeScript and React Flow; Playwright for browser acceptance checks. These are choices for this plan, not installed dependencies. Resolve compatible stable versions and pin them in project-local lockfiles in Tasks 1 and 9. No additional charting package is required initially.

**Spec:** [Phase 1 specification](../../specifications/phase-1-specification.md), SHA-256 `582ea8aefdec561230249c569e38cd1ee0b434b4740e9cd67f6d68a89828f3f8` at planning time. Read both documents before execution. The specification governs behavior; this plan supplies implementation order, boundaries and checks.

**Status:** Implementation plan for review, 16 October 2026. Creating this plan does not create source scaffolding or authorize installation, execution against live models, migration, registration changes, commits or deployment.

## Global constraints

- The only new file created by this planning task is this document. All paths below are proposed future paths relative to `C:\AI\bridge-monitor` unless an existing reference is explicitly identified.
- Do not modify existing bridge source, configuration, documentation, Git state or model/client registrations. Reuse requires copying only selected material into the new project with provenance; do not patch the old checkout or run its installation scripts.
- Keep `.venv`, dependency caches, `dashboard/node_modules`, build output, logs, fixtures, reports, local PostgreSQL data and any isolated test checkouts inside this project. Use project-owned processes and a dedicated database; never run reset/cleanup commands against existing projects or databases.
- GPT owns Claude fan-out. Claude can delegate only explicitly permitted Qwen work. No Claude-to-Claude spawning or Qwen agent spawning. A coordinator-owned recovery has the scope and ownership restrictions in specification section 9.
- Use Next.js without per-request SSR, SSE for live browser updates and normal HTTP for queries/actions. PostgreSQL is the sole initial database. No broker, Neo4j, training pipeline, automatic retrieval or learned routing.
- Record requested and reported model identities separately. Retain exact observable original/effective prompts and raw/delivered responses, visible tools, transformations, partial capture and unknowns. Do not invent hidden context or missing model information.
- Count distinct work items separately from calls, attempts and delivered result units. “Not routed” requires an explicit paid-route decision and no actual local dispatch. Post-dispatch paid replacement is takeover only after confirmed work resolution.
- A local timeout automatically continues paid work in a compatible healthy owner without an approval pause. In-owner continuation consumes time and tokens. Dispatch winning a queue-expiry race follows its execution deadline and shared cutoff, not an immediate execution timeout.
- Preserve durable ownership, uncertain reservations, restricted child capabilities and source isolation from the first live slice. Increasing concurrency later must not replace these invariants with a new scheduler.
- Initial configurable limits: Claude active globally **2**, per root **2**, waiting **8**; Qwen active **1**, waiting **4**; outstanding local requests per Claude execution **1**; Claude execution deadline **900 seconds**, Qwen **180 seconds**. The single-worker pilot explicitly overrides Claude capacity to 1 in its own policy file.
- Queue deadlines, context-wait deadline, fallback allowance, client timeout/delivery margin, budgets, endpoints, ports and retention values are required deployment inputs where the specification supplies no default. Do not invent measured-safe values or silently substitute fallback defaults. Validate them before live admission.
- Soft reset validates and activates configuration atomically; running work retains its specified pins, while current capacity and withdrawal restrictions apply as specified. No automatic policy updates from observed results.
- Every milestone includes its relevant tests. Milestone 5 expands failure coverage; it is not permission to defer basic correctness until the end.

**Suggested execution model: Claude Opus** for scheduler ownership, fallback and recovery tasks, because their state transitions cross process boundaries and affect duplicate execution. **Claude Sonnet** is suitable for routine storage, queries and dashboard tasks against these contracts. Advisory only; the user's chosen model remains valid and no model is switched by this plan.

## Review focus

These conditions receive explicit tests in the owning tasks:

1. A request is accepted by a model runtime but the adapter loses acknowledgement: hold uncertainty and do not replay it automatically. Tasks 4, 7 and 16.
2. Producer events arrive out of order, and one database transaction commits after another: replay must neither skip a committed event nor invent causality. Tasks 2 and 8.
3. An authenticated actor submits another worker's result ID, or required content is oversized/unavailable: reject false attribution and expose incompleteness without rerunning useful inference. Tasks 1, 6 and 14.
4. A pack/example is withdrawn while a child waits in a queue: the dispatch check must override the old admission pin, with a reproducible reason. Tasks 13 and 14.
5. A viewer reconnects while reading a long response: preserve selection and scroll, recover gaps visibly, and keep content access authenticated. Tasks 8, 9 and 11.
6. Capture is degraded or the spool overflows: expose the gap and apply configured new-work admission behavior before the first live pilot. Tasks 5, 8, 10 and 17.
7. The first live launch uses reviewed policy, isolated client capabilities and validated no-guidance readiness. Tasks 1, 4, 5 and 7; Tasks 13–14 extend those same paths.

## Delivery sequence

| Milestone | Tasks | Demonstrable result | Dependency |
|---|---|---|---|
| 1. Contracts and persistence | 1–3 | Synthetic exchanges persist and reproduce the agreed statistics | None |
| 2. One complete live flow | 4–7 | One observable GPT → Claude → Qwen exchange, including dispositions and paid fallback | Milestone 1 |
| 3. Live dashboard | 8–11 | Live flow, history and side-by-side response inspection | Synthetic development may start after Milestone 1; live exit requires Milestone 2 |
| 4. Multiple workers and policy | 12–15 | Shared worker limits, reloads, registered guidance and example reuse metadata | Milestones 2 and 3 complete |
| 5. Recovery and controlled adoption | 16–18 | Verified recovery behavior and a reversible pilot runbook | Milestone 4 |

These are delivery increments within the agreed Phase 1. Do not expose unfinished later capabilities as production-ready. Each milestone produces reviewable evidence under `docs/verification/`; raw test logs and screenshots go under `var/reports/`. Neither directory is created by this planning task.

After Milestone 1, Tasks 8–11 may proceed against synthetic events through the real persistence/query contracts while Tasks 4–7 remain in progress. Missing live credentials or runtime facts do not block that dashboard work, but cannot satisfy either milestone's required live evidence. Milestone 3 closes only after Milestone 2 and its own live integration checks. This overlap changes sequencing only; it does not authorize delegated agents or change the approved dashboard design.

## File ownership and shared interfaces

| Area | Responsibility | First owner |
|---|---|---|
| `contracts/` | Versioned envelopes, policy, request/result, disposition and query types | Task 1 |
| `database/migrations/` | `001`: events, decisions, read projections and publication; `002`: operational state including fallback/deadline/recovery records; `003`: guidance registry and example reviews | Tasks 2, 4, 14 respectively |
| `bridge/adapters/` | Protocol-specific observations and restricted execution | Task 5 |
| `bridge/runtime/` | Scheduling, fenced ownership, timeout fallback, reconciliation | Tasks 4, 7 |
| `bridge/guidance/` | Basic no-guidance readiness, then registered snapshots and selection checks | Tasks 7, 14 |
| `service/ingest/`, `service/queries/` | Transactional capture, history and statistics | Tasks 2, 3 |
| `service/http/` | Authenticated API, snapshot and SSE delivery | Tasks 2, 8 |
| `dashboard/` | Browser application and generated contract types | Tasks 9–11 |
| `config/` | Project-local deployment and pilot policy examples | Tasks 1, 4; extended in 13 |
| `scripts/` | Project-scoped verification, inspection, reconciliation and operator commands | Tasks 1, 3, 4; extended in 13–18 |
| `tests/` | Contract, integration, concurrency, recovery and pilot checks | Each task |
| `var/` | Ignored project-local runtime data, spool, logs and reports | Tasks 1, 18 |

Names used by subsequent tasks are fixed here. Implementations may split a file when necessary, but changing these public contracts requires updating their producers and consumers together.

| Contract or operation | Inputs and output |
|---|---|
| `EventEnvelope`, `ContentBatch`, `ProducerIdentity` | Specification section 5 identity, sequence, causal links, timestamps, origin, payload and content references; identity is server-bound, not caller-asserted authority |
| `PolicySnapshot`, `PolicyValidation` | Validated tuning/capability policy, source hash, review references, immutable version and validation errors |
| `TaskPacket`, `CallRequest`, `ExecutionBinding` | Root scope/category/budgets; child work and caller; server-bound worker incarnation, parent attempt and model evidence |
| `ResultReceipt`, `CompletionEnvelope`, `DispositionRecord` | Exact result identity/hash, paid answer and contributor/fallback references, consumer judgments and work resolution |
| `AdmissionDecision`, `DispatchLease`, `FallbackTransition` | Accepted/queued/paid-direct decision; fenced capacity owner; child-call-keyed fallback state and recipient |
| `CohortQuery`, `MetricSet`, `DashboardSnapshot`, `PublicationCursor` | Definition/version/as-of filters; numerator/denominator/coverage values; consistent live/read snapshot and durable replay position |
| `GuidanceSelection`, `GuidanceManifest`, `GateDecision`, `ExampleReview` | Registered selections, actual supplied references, validated facts/outcomes/reasons and authenticated manual reuse review |
| `ingest_batch` | Accepts envelopes, referenced content and authenticated producer; returns per-event committed, duplicate or rejected receipts |
| `admit_call`, `claim_dispatch`, `commit_fallback` | Accept a bound request and policy; return admission, fenced lease, or existing/new fallback transition, respectively |
| `record_completion` | Accepts completion envelope plus execution binding; returns answer receipt and separate accounting completeness |
| `query_metrics`, `read_snapshot`, `read_publications` | Accept cohort or cursor filters; return metric set, snapshot, or ordered committed event page |
| `resolve_guidance`, `check_readiness` | Accept selection, trusted task scope, policy and current restrictions; return immutable manifest and gate decision |

The event contract is shared through generated schema/types. The dashboard must not maintain an independently invented event enum or metric formula. Scheduler mutations use dedicated operations with validated ownership; arbitrary telemetry producers cannot fabricate dispatch reservations by sending status events.

### Verification conventions

Python commands below run from the project root using `.\.venv\Scripts\python.exe`. The environment and dependencies are created only during authorized execution. Browser commands run in `dashboard/`. Define the named test files and scripts in their owning tasks before invoking them; these are future commands, not claims that tests exist today.

Each task first specifies behavioral tests, verifies the expected failure against the missing behavior, implements the minimal slice, then reruns those tests. Test failures should demonstrate the named missing behavior, not a broken installation. PostgreSQL integration tests use a dedicated project test database and reject a DSN pointing to the operational database. Tests never reset a shared database. Do not add tests that only repeat implementation details.

Live-model tests are opt-in through the `--live-models` option implemented in `tests/conftest.py`; ordinary test runs must neither call paid models nor contact Skynet. Milestone 2 uses an explicit no-guidance pilot category with validated scope, context budget and fallback compatibility. Registered guidance is not advertised or accepted until Task 14 implements its full checks.

## Milestone 1 — Contracts and persistence

### Task 1 — Define contracts and validated project configuration

**Files to create:** `pyproject.toml`, `requirements.lock`, `.gitignore`, `contracts/events.py`, `contracts/content.py`, `contracts/policy.py`, `contracts/work.py`, `contracts/outcomes.py`, `contracts/queries.py`, `contracts/guidance.py`, `contracts/export.py`, `config/bridge-policy.example.toml`, `tests/conftest.py`, `tests/contracts/test_events.py`, `tests/contracts/test_policy.py`, `scripts/verify-paths.ps1`.

**Interfaces:** Produces all shared types listed above and `validate_policy`, which accepts proposed policy and adapter capability facts and returns `PolicyValidation` plus an immutable `PolicySnapshot` only on success. Schema export produces machine-readable event and API types for Task 9.

Task 1 owns the capacity/status and capture-health event payloads and corresponding `DashboardSnapshot` fields: active and queued work, held-uncertain capacity, blocked starts/reasons, spool pressure, degraded capture, known gaps and recovery. Contract tests cover these states and explicit unknowns. Tasks 4–5 emit this vocabulary; they do not first define it. Synthetic dashboard consumers use the same exported contracts.

- [ ] Define tests for required identity/version/sequence, explicit unknown model/capture states, UTC times, invalid disposition values, required deployment values, and the exact default limits. Validate review/approval provenance for the policies requiring it in the specification, including fallback policy, on initial activation as well as reload. Distinguish content limits from silent truncation and producer identity from untrusted payload fields.
- [ ] Establish the isolated Python environment and pin compatible stable dependencies under this project; run the two contract test files and confirm the intended missing-contract failures.
- [ ] Implement validation, schema export, project-only path checks and policy examples. Document required deployment fields without inserting operational credentials or guessed endpoints. Make `var/`, local secrets, caches and generated output ignored if Git is later initialized.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/contracts -q`; all contract tests must pass. Run `scripts/verify-paths.ps1` in report-only mode and confirm every planned runtime/output path resolves inside this project.

### Task 2 — Persist exact content and idempotent events

**Files to create:** `database/migrations/001_event_store.sql`, `database/migrate.py`, `service/ingest/store.py`, `service/ingest/projector.py`, `service/ingest/publication.py`, `service/http/app.py`, `service/http/auth.py`, `service/http/ingest.py`, `tests/integration/test_ingest.py`, `tests/integration/test_content.py`, `tests/integration/test_auth.py`.

**Interfaces:** Implements `ingest_batch`; exposes authenticated `POST /api/v1/events/batch`. Returns receipts without acknowledging uncommitted data. Establishes core task/work/session/call/attempt/model, content/message/result, disposition/evaluation/config and event tables from specification section 11, plus durable publication positions and their transactional counter in `001_event_store.sql`. Later migrations add operational and guidance records.

The `001` migration includes `delegation_decisions` and the read projections for Task 1's capacity/status and capture-health events. `projector.py` materializes those observations without requiring runtime tables or granting dispatch authority; the ingestion tests verify synthetic capacity/gap/recovery events round-trip into those projections. Guidance manifests and gate facts use versioned JSONB on these existing event/decision records, including the no-guidance baseline. No separate manifest table or store is introduced.

- [ ] Define integration cases for duplicate IDs, reused producer sequences with conflicting payloads, late references, out-of-order chunks, exact byte round trips, partial/deleted content, unauthorized producer/consumer identities and transaction rollback. Include two writers committing in reverse start order.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_ingest.py tests/integration/test_content.py tests/integration/test_auth.py -q` against the dedicated database; confirm missing transaction/auth behavior fails.
- [ ] Implement additive migrations, relational identities, exact content storage plus structured payloads, unresolved-reference reconciliation and producer-bound ingestion. Commit content, facts, read projections and publication positions transactionally. Serialize publication allocation through a row-locked transactional counter so a later position cannot commit ahead of an uncommitted earlier position; an ordinary sequence is not proof of commit order. Authenticate producer tokens separately from browser/operator sessions; never store credentials in event content.
- [ ] Rerun the same checks. A rolled-back transaction yields no success receipt or publication; duplicate delivery yields one logical observation; committed exact content is retrievable only with authorized access. Reversed-start-order writers must produce a committed publication stream that cannot skip an event. Verify application queries cannot alter ownership state through event payloads.

### Task 3 — Implement cohorts, statistics and seed replay

**Files to create:** `service/queries/cohorts.py`, `service/queries/metrics.py`, `service/queries/history.py`, `tests/fixtures/demonstration.json`, `tests/fixtures/edge-cases.json`, `tests/integration/test_metrics.py`, `tests/integration/test_history.py`, `scripts/replay-fixtures.py`, `scripts/inspect-exchange.py`.

**Interfaces:** Implements `query_metrics` over `CohortQuery`; returns `MetricSet` with counts, denominators, stage, definition version, as-of cutoff and coverage. History operations return metadata pages and explicit content references, not all full bodies. The small authenticated operator inspection command reuses these queries and content access checks, allowing Milestone 2 evidence to be inspected before dashboard delivery.

- [ ] Define the 100-work-item synthetic cohort and edge cases for retries, decomposition, mixed guidance, superseded/conflicting dispositions, paid-direct work, unknown final lineage and late undelivered responses. Include separate capacity/status and capture-health fixtures using Task 1's contracts, covering active/queued/held-uncertain/blocked states and spool pressure/gaps/recovery without changing the statistics cohort. Pin the expected numerator and denominator of every measure.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_metrics.py tests/integration/test_history.py -q` and confirm missing grouping/formula behavior fails.
- [ ] Implement leaf-frontier/as-of selection, result-unit accounting, separate consumer stages, bounded history queries and synthetic-origin exclusion. Add the exchange inspection command and cover its authorized/unauthorized access in the history tests. Preserve report parameters so a later acknowledgement cannot silently change an exported as-of result.
- [ ] Rerun the tests and fixture replay twice. Both replays must yield routed 40%, not routed 60%, coverage 90%, confirmed acceptance 60%, reported acceptance 66.7%, contribution 75%, takeover 10% and local resolution 24%. Empty denominators must yield N/A, and production-default queries must exclude these synthetic records.

**Milestone 1 exit checks**

- [ ] A fresh dedicated database migrates and stores/replays the demonstration without duplicate counts or changed bytes.
- [ ] Contract validation, unauthorized-access cases, commit-ordered publication and cohort assertions pass; the inspection command retrieves the exact synthetic exchange.
- [ ] Capacity/status and capture-health fixtures populate Task 2's read projections using only `001_event_store.sql`. Their exported contracts and persisted observations are ready for Tasks 8–11 without Milestone 2 tables or adapters.
- [ ] Record schema version, dependency versions, test commands and results in `docs/verification/milestone-1.md`.

## Milestone 2 — One complete live flow

### Task 4 — Add durable admission and single-worker ownership

**Files to create:** `database/migrations/002_runtime_state.sql`, `bridge/runtime/admission.py`, `bridge/runtime/scheduler.py`, `bridge/runtime/ownership.py`, `bridge/runtime/workspaces.py`, `bridge/runtime/reconciliation.py`, `bridge/runtime/policy_startup.py`, `scripts/reconcile-execution.py`, `tests/integration/test_admission.py`, `tests/integration/test_ownership.py`, `tests/integration/test_reconciliation.py`, `tests/integration/test_policy_startup.py`, `config/pilot-policy.example.toml`.

**Interfaces:** Implements `admit_call` and `claim_dispatch`; returns `AdmissionDecision` and `DispatchLease`. PostgreSQL owns jobs, reservations, incarnation/fencing tokens and budgets; process memory is only a cache. Pilot configuration uses one active Claude execution without changing the documented default of two.

Task 4 owns `002_runtime_state.sql`, including durable deadline/cutoff state, child-call-keyed fallback transitions and recovery ownership/recipient/lineage records needed by Tasks 7 and 12. Its ownership tests verify persistence, references and uniqueness constraints; the later tasks implement and test the consuming transitions. Explicit paid-route decisions remain in Task 2's `delegation_decisions`, linked and committed atomically with the corresponding fallback transition. Manifests and gate facts remain in the existing event/decision records, not operational manifest tables.

Implements `reconcile_execution` with execution ID, evidence and authenticated operator/system provenance, returning confirmed live/terminated/uncertain state. Initial startup uses Task 1 policy validation, validates required review/approval references, and durably binds the active version/hash before dispatch. Runtime ownership and reconciliation publish status observations through Task 2 ingestion; read projections never grant ownership.

- [ ] Define competing-process claims, expired-but-unconfirmed owner, lost dispatch acknowledgement, root budget rejection and shared-checkout access cases. Test evidence-backed reconciliation and rejected unproven clearance; heartbeat expiry or a resident-model list cannot prove individual execution termination. Test startup rejection for missing required review references and activation version/hash binding.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_admission.py tests/integration/test_ownership.py tests/integration/test_reconciliation.py tests/integration/test_policy_startup.py -q`; verify missing arbitration, reconciliation and startup validation fail.
- [ ] Implement transactional admission, capacity reservations and dispatch fencing, with immutable task pins and source-snapshot references. Provide the minimal authenticated reconciliation command before live use: retain uncertainty without inference replay and release capacity only on confirmed termination or authorized evidence-backed reconciliation. Use source locks/snapshots independently of paid/local capacity. Validate and activate reviewed startup policy through one shared path for later reload extension.
- [ ] Rerun tests with two separate scheduler processes competing for one slot. Exactly one may obtain dispatch ownership; restarting a process must reconcile before new dispatch and must not grant a second launch or source-write permission. Confirm operator reconciliation can resolve held capacity with appropriate evidence and cannot clear it with an unsupported assertion.

### Task 5 — Connect restricted adapters and intermediate telemetry

**Files to create:** `bridge/adapters/base.py`, `bridge/adapters/claude_cli.py`, `bridge/adapters/qwen_runtime.py`, `bridge/adapters/codex_ingress.py`, `bridge/mcp_server.py`, `bridge/telemetry/emitter.py`, `bridge/telemetry/spool.py`, `tests/adapters/test_claude.py`, `tests/adapters/test_qwen.py`, `tests/adapters/test_capture.py`, `docs/operations/adapter-capabilities.md`. **Files to modify:** `bridge/runtime/admission.py` for configured new-work admission during degraded capture.

**Interfaces:** Adapters consume `TaskPacket` and `DispatchLease`, emit `EventEnvelope`/content, and return a result receipt or uncertainty. The new bridge exposes `delegate_claude`, `delegate_local_analysis`, `complete_work` and `get_execution_status` operations. Execution bindings are supplied by the bridge, never accepted as model-asserted identity.

Capture-health observations use Task 1's event contracts and Task 2 ingestion/projections as well as the immediate status operation. During a collector outage, retain/replay available gap and recovery evidence through that same path; the dashboard does not depend on a separate adapter-only status vocabulary.

- [ ] Define fake-runtime tests for request transformation, chunks/final-only output, reported-model absence, visible tool events, accepted request followed by connection loss, and capability denial. Include collector outage, spool overflow, explicit capture gaps and configured new-work admission while capture is degraded. Verify stdout contains only protocol traffic.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/adapters -q` and verify missing adapter behavior fails.
- [ ] Implement the adapters and durable bounded spool with configurable overflow and new-work admission policies. Expose degraded capture/gaps in the initial status operation and operator report, retaining gap evidence where possible across collector recovery; never claim complete capture across known loss. Limit Claude's permitted child operation to the named local-analysis capability; preserve parent scope. Inspect the existing bridge only as a read-only reference and record any copied material's source/hash. Discover exact Qwen profile, context limits, cancellation evidence and client timeout capabilities before live admission. Unknown capabilities cannot be claimed as supported.
- [ ] Rerun fake-runtime checks. Prepare a temporary, project-local client configuration for the new endpoint; inspect the worker's effective tools, registrations and hooks to verify only its bridge-granted capabilities are available. If isolation requires changing an existing client configuration, stop that live-integration step and present the exact proposed change; do not silently edit existing configuration. Record this preflight and exact project-local development startup/migration/stop commands in the adapter capability document. A pilot must capture request/queue/dispatch/intermediate lifecycle events before terminal completion, with final-only limitations explicitly marked where applicable.

### Task 6 — Bind results, dispositions and downstream lineage

**Files to create:** `bridge/runtime/completions.py`, `service/ingest/lineage.py`, `tests/contracts/test_completion_envelope.py`, `tests/integration/test_dispositions.py`, `tests/integration/test_lineage.py`.

**Interfaces:** Implements `record_completion` for `CompletionEnvelope` plus trusted `ExecutionBinding`; returns the delivered answer receipt and independent accounting completeness. Supports later acknowledgements without a new inference.

- [ ] Define accepted/revised/replaced/unused cases, missing/invalid acknowledgements, spoofed actor/result hashes, part-level outcomes, cycles, supersession conflicts, correction-execution links and absent GPT/human acknowledgement.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/contracts/test_completion_envelope.py tests/integration/test_dispositions.py tests/integration/test_lineage.py -q`; confirm validation and lineage failures are exposed.
- [ ] Implement consumer-stage obligations, contribution links, repair evidence and work resolution. Preserve a useful returned answer when accounting is missing; do not manufacture final acceptance or call a model again to complete metadata.
- [ ] Rerun tests. A Claude-accepted Qwen response with unknown GPT disposition must show stage acceptance but unknown final survival. Never-delivered late results must create neither a consumer obligation nor an R denominator entry.

### Task 7 — Implement timeout continuation and demonstrate the live slice

**Files to create:** `bridge/runtime/deadlines.py`, `bridge/runtime/fallback.py`, `bridge/guidance/gates.py`, `tests/integration/test_fallback.py`, `tests/integration/test_fallback_accounting.py`, `tests/integration/test_pilot_readiness.py`, `tests/pilot/test_single_flow.py`, `docs/verification/milestone-2.md`. **Files to modify:** `bridge/runtime/admission.py`, `bridge/runtime/scheduler.py` for readiness before local reservation/dispatch, and `tests/conftest.py` for the explicit live-model opt-in.

**Interfaces:** Implements `commit_fallback` keyed by child call ID. A healthy owner receives a structured continuation signal within its existing execution; the completion envelope references that transition. The same operation atomically records the explicit paid-route decision when fallback commits.

Uses Task 4's `002` deadline/fallback records and Task 2's event/decision storage. No-guidance manifests and readiness facts are versioned payloads on those existing records. This task adds behavior against the established schema, not another migration or manifest store.

Implements the no-guidance baseline of `check_readiness` using the trusted task category/scope, required context, total prompt plus reserved-output budget, and compatible fallback profile established in Task 5. Registered guidance remains unavailable until Task 14 extends this same check. Uses Task 4 reconciliation for uncertain executions; no new recovery mechanism is introduced.

- [ ] Define queue-expiry/dispatch races, completed-result/fallback races, repeated timeout notifications, retries after fallback, insufficient budgets, shared stage cutoff, client deadline margin, cancellation, late delivery and missing completion envelopes. Test no-guidance readiness with unknown/out-of-scope classification, missing required context, unvalidated/oversized total context and incompatible fallback. Assert in-owner work has real usage without an invented additional call.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_fallback.py tests/integration/test_fallback_accounting.py tests/integration/test_pilot_readiness.py -q`; verify each transition and readiness check fails before implementation.
- [ ] Implement atomic arbitration, budget/deadline reservation and release, compatible-owner continuation, explicit terminal fallback states and uncertainty. No Qwen reservation is granted before readiness passes. Missing classification/context follows the bounded context-wait and explicit paid-route rules; unknown required compatibility cannot pass. Record a no-guidance manifest and gate facts. A dispatch winner uses the execution deadline; an expiry winner forbids later local dispatch/retry. Parent failure is a separate recovery condition, not a reason to replay its uncertain inference. Late local output stays outside R until actual delivery.
- [ ] Rerun integration tests. Once live calls are explicitly authorized, startup policy is activated and Task 5 client isolation/capability preflight passes, run `.\.venv\Scripts\python.exe -m pytest tests/pilot/test_single_flow.py -q --live-models` using the isolated client configuration. Demonstrate one normal exchange and one controlled local timeout, with paid continuation and captured completion. Inspect captured bodies and lineage using Task 3's operator command. Mark unavailable GPT transcript/final acknowledgement as unknown rather than passing a fabricated full-chain check.

**Milestone 2 exit checks**

- [ ] Inspect one real exchange from captured prompt to result, disposition and final observable resolution; record actual model evidence and capture boundaries.
- [ ] Demonstrate automatic paid continuation without an approval pause, retaining the uncertain local reservation when termination cannot be proved.
- [ ] Confirm pre-dispatch paid fallback is not routed, while post-dispatch confirmed paid replacement is delegated takeover; unresolved paid output is not takeover.
- [ ] Record startup policy version/hash and required review references, effective client capability isolation, passing no-guidance readiness, capture-outage/overflow admission checks and evidence-backed reconciliation checks in `docs/verification/milestone-2.md` before declaring the first live slice ready.
- [ ] Live pilot evidence is required to call this milestone complete. If credentials, client support or runtime facts are unavailable, mark the milestone incomplete rather than substituting synthetic success.

## Milestone 3 — Live dashboard

Tasks 8–11 can start after Milestone 1 with synthetic fixtures. Their APIs read the core event projections/publications; synthetic development does not require Milestone 2 operational tables or running model adapters. Milestone 1 defines and projects capacity/status and capture-health events; Milestone 2 adds live producers for those same contracts. The live exit checks below still require Milestone 2 completion.

### Task 8 — Publish consistent snapshots and resumable SSE

**Files to create:** `service/queries/snapshot.py`, `service/http/query.py`, `service/http/stream.py`, `tests/integration/test_snapshot_stream.py`, `tests/integration/test_replay_gap.py`.

**Interfaces:** Implements `read_snapshot` and `read_publications` over Task 2's established commit-ordered publications. Endpoints: `GET /api/v1/snapshot`, `/stream`, `/work-items`, `/work-items/{id}`, `/content/{id}` and `/metrics`. Queries use `CohortQuery`; SSE resumes with `PublicationCursor` and exposes snapshot reset when the cursor is outside retained history. Snapshots distinguish active, queued and held-uncertain/blocked capacity and include capture health and known gaps from the first dashboard slice.

- [ ] Define writes during snapshot creation, duplicate replay, reconnect after retention expiry, bounded pages and unauthorized content/stream access. Verify Task 2 commit ordering survives snapshot/replay and synthetic fixtures expose uncertain capacity and capture gaps without runtime-table dependencies.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_snapshot_stream.py tests/integration/test_replay_gap.py -q`; verify missing commit-order and snapshot guarantees fail.
- [ ] Read snapshot/projections and their Task 2 publication cursor consistently. Serve the existing committed stream without backfilling or reordering earlier live events. SSE notifications are hints; reconnect reads committed rows. Keep read models independent of scheduler ownership mutations.
- [ ] Rerun tests. No committed event may be skipped across snapshot plus replay. Expired cursors must produce an explicit gap and fresh snapshot, not an apparently continuous stream.

### Task 9 — Build the client shell and authenticated data connection

**Files to create:** `dashboard/package.json`, `dashboard/package-lock.json`, `dashboard/next.config.ts`, `dashboard/app/layout.tsx`, `dashboard/app/page.tsx`, `dashboard/app/globals.css`, `dashboard/components/DashboardClient.tsx`, `dashboard/lib/api.ts`, `dashboard/lib/events.ts`, `dashboard/lib/contracts.generated.ts`, `dashboard/playwright.config.ts`, `dashboard/tests/session.spec.ts`, `dashboard/tests/reconnect.spec.ts`. **Files to modify:** `service/http/app.py` and `service/http/auth.py` for static hosting and browser sessions.

**Interfaces:** Consumes generated contract types and Task 8 endpoints. A static shell mounts the dashboard in the browser; SSE/auth cookies use the same service origin. The service hosts static output and API routes together, with deployment binding/TLS configured explicitly.

- [ ] Define browser checks for authenticated login/session expiry, unauthorized body requests, reconnect/gap reset, visible offline state and no event collection interruption when display is paused.
- [ ] Set up and lock the proposed frontend dependencies locally. Run `npm run test:e2e -- session.spec.ts reconnect.spec.ts` in `dashboard/`; confirm the missing client behaviors fail.
- [ ] Implement static export with browser-only dashboard loading, typed API access and deduplicated SSE state. Use an opaque HttpOnly operator session with configured credentials; keep producer credentials out of the browser and credentials out of URLs/logs. Do not add an account-management subsystem. Require the configured transport protection before exposing sensitive content beyond local development.
- [ ] Run `npm run typecheck`, `npm run build` and the browser checks. Serve the export without a Next.js SSR process and verify the application loads its operational data client-side.

### Task 10 — Build the grouped flow and response inspector

**Files to create:** `dashboard/components/FlowGraph.tsx`, `dashboard/components/WorkerGroup.tsx`, `dashboard/components/WorkList.tsx`, `dashboard/components/ExchangeInspector.tsx`, `dashboard/components/ResponseComparison.tsx`, `dashboard/components/ExecutionTimeline.tsx`, `dashboard/tests/flow-inspector.spec.ts`.

**Interfaces:** Consumes snapshot/work-item detail and on-demand content. Uses one selected work-item/result identity across graph, list and inspector; individual executions are expandable children of the Claude group.

- [ ] Define browser scenarios from the approved mockup: two displayed executions using synthetic data, selected route focus, prompt transformation views, raw/delivered responses, side-by-side correction, unknown model identity and partial/deleted content. Include queued versus active versus held-uncertain capacity, blocked starts, degraded capture and known gaps.
- [ ] Run `npm run test:e2e -- flow-inspector.spec.ts`; verify the missing interactions fail.
- [ ] Implement the dark theme and grouped layout with React Flow, request/return cues, accessible status labels, explicit current-versus-historical activity and the Exchange/Decision/Timeline inspector. Show held uncertainty/blocked capacity and capture health within the approved groups/status area from this first slice. Fetch bodies only on selection. Keep text diff separate from semantic acceptance.
- [ ] Rerun the browser scenario and capture screenshots under `var/reports/dashboard/`. Check against the existing mockup at desktop and narrow widths, including keyboard focus, readable contrast and reduced-motion behavior. Preserve the approved mockup asset unchanged.

### Task 11 — Connect metric filters and stable live/history behavior

**Files to create:** `dashboard/components/MetricStrip.tsx`, `dashboard/components/CohortControls.tsx`, `dashboard/lib/selection.ts`, `dashboard/tests/metrics-history.spec.ts`, `dashboard/tests/reading-position.spec.ts`, `docs/verification/milestone-3.md`.

**Interfaces:** Uses server-provided `MetricSet`, `CohortQuery` and result lineage. Client filters are explicit query parameters; the UI never calculates a competing acceptance formula.

- [ ] Define tests for the exact demonstration statistics, metric-to-list filtering, consumer-stage selection, pending versus not routed, N/A denominators, selected-result persistence and long-response scroll during updates.
- [ ] Run `npm run test:e2e -- metrics-history.spec.ts reading-position.spec.ts`; confirm the missing behavior fails.
- [ ] Implement numerator/denominator/coverage explanations, history filters, body search/copy/expand, pause-display behavior and visible stale/replay state. Keep the live map labelled current while the selected historical cohort controls metrics/list.
- [ ] Rerun browser checks and frontend build/type checks. A user reading a response must not lose position or selection as unrelated events arrive; replayed events must not duplicate rows or counts.

**Milestone 3 exit checks**

- [ ] The historical Milestone 2 exchange remains inspectable after service/browser restart. A new, explicitly authorized live exchange using the same single-flow scenario appears as it happens; replaying the earlier exchange is not evidence of live integration.
- [ ] Synthetic metrics match the backend, and historical filters do not silently alter the current topology view.
- [ ] Static browser rendering, authenticated content, SSE recovery and side-by-side inspection pass end-to-end checks.
- [ ] Record screenshots, commands and observed limitations in `docs/verification/milestone-3.md`.

## Milestone 4 — Multiple workers and policy

### Task 12 — Enable shared multi-worker scheduling and source isolation

**Files to modify:** `bridge/runtime/admission.py`, `bridge/runtime/scheduler.py`, `bridge/runtime/ownership.py`, `bridge/runtime/workspaces.py`. **Files to create:** `tests/concurrency/test_limits.py`, `tests/concurrency/test_fairness.py`, `tests/concurrency/test_workspaces.py`, `tests/concurrency/test_paid_recovery.py`.

**Interfaces:** Extends the same admission/lease contracts and Task 4 reconciliation; does not replace them. Adds coordinator-owned child recovery only after confirmed owner termination, with the original owner's caller as recipient and no parent-attempt replay. Minimal reconciliation must already pass before these recovery tests or live concurrency begin.

Recovery uses the ownership/recipient/lineage records already owned by Task 4's `002` migration and references existing calls, attempts and results. No second recovery schema or migration is required here.

- [ ] Define multi-process tests for global/per-root limits, round-robin roots/FIFO within roots, separate local capacity, paid owner waiting on local work, shared-checkout serialization, stable read snapshots and confirmed-termination recovery.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/concurrency -q`; demonstrate the missing multi-worker behavior fails.
- [ ] Enable the configured default of two paid workers, separate read/write locks, narrowly scoped isolated workspaces and durable recovery lineage. No waiting parent may occupy capacity needed by its own replacement in the same paid pool. Cancellation reaches managed fallback/recovery descendants.
- [ ] Rerun tests with independent bridge processes and competing root tasks. Active counts never exceed configured limits; permitted work progresses without starvation or pool deadlock, and isolated work cannot write to another task's sources.

### Task 13 — Activate reviewed policy reload and restart handling

**Files to create:** `bridge/runtime/policy_reload.py`, `scripts/policy-control.py`, `tests/integration/test_policy_reload.py`, `tests/integration/test_policy_pins.py`. **Files to modify:** `contracts/policy.py`, `bridge/runtime/policy_startup.py`, `bridge/runtime/admission.py`, `config/bridge-policy.example.toml`.

**Interfaces:** `activate_policy` accepts proposed policy plus authenticated operator/review provenance and returns activated/rejected/restart-required status with version/hash. Extends Task 4's startup activation path using the same Task 1 validator and review requirements, rather than introducing a second validator. Restart uses the existing Task 4 reconciliation before dispatch. No browser policy editor is required.

- [ ] Define malformed/unknown-key policy, missing review reference, two-to-one capacity reduction, increased capacity, smaller queues, child-call pins, heartbeat changes and restart-required settings.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_policy_reload.py tests/integration/test_policy_pins.py -q`; verify absent atomic activation/pinning fails.
- [ ] Implement reload through a project-local operator command. Preserve last-known-good policy on rejection, already admitted queue entries and active work. Current global limits govern new starts; exact current restriction/review references accompany dispatch checks. Restart reconciles before dispatching.
- [ ] Rerun checks. With two active Claude executions, lowering capacity to one must kill neither; no additional job starts until capacity permits. Rejected configuration must change neither effective limits nor activation version.

### Task 14 — Register guidance and enforce the two basic gates

**Files to create:** `database/migrations/003_guidance.sql`, `bridge/guidance/registry.py`, `bridge/guidance/selection.py`, `scripts/guidance-control.py`, `tests/integration/test_guidance.py`, `tests/integration/test_gate_dispatch.py`. **Files to modify:** `bridge/guidance/gates.py`, `bridge/runtime/admission.py`, `bridge/runtime/scheduler.py`, `contracts/guidance.py`.

**Interfaces:** Implements `resolve_guidance` and extends Task 7's `check_readiness` for registered guidance. Preserve the existing no-guidance checks and deadline/fallback behavior. Registration stores immutable bytes/hash; approved configuration selects registered references; the bridge inserts them and emits the actual manifest. Gate outcomes feed the existing delegation decision, not a new routing subsystem.

Task 14 owns `003_guidance.sql` for `guidance_pack_versions` and the append-only `example_reviews` storage consumed by Task 15. Registered guidance reuses Task 2's event/decision manifest and gate payloads already used by Task 7; guidance bodies continue to use `content_objects`. It does not introduce a second manifest store.

- [ ] Define modified authoring files, unapproved/rejected/evaluation-only items inside approved packs, incompatible scope, unknown classification, missing optional versus required guidance, total context budget and withdrawal after queue admission.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_guidance.py tests/integration/test_gate_dispatch.py -q`; verify missing registered-content and dispatch checks fail.
- [ ] Implement approved snapshot resolution, trusted packet/category validation, required-context readiness, optional omission recording and fresh withdrawal/item-use checks at dispatch. Inline caller text stays task context; do not claim semantic leakage detection. Context/classification waiting shares Task 7 deadlines and paid continuation behavior.
- [ ] Rerun tests, including `tests/integration/test_pilot_readiness.py` to preserve the no-guidance baseline. A queue-time withdrawal prevents local dispatch despite its old pin. Missing optional guidance permits an otherwise valid task; missing required content never passes through disclosure alone. Gate rejection is not recorded as a Qwen execution failure.

### Task 15 — Record example reuse, evidence and guidance drill-downs

**Files to create:** `service/ingest/example_reviews.py`, `scripts/example-review.py`, `dashboard/components/GuidanceDetails.tsx`, `tests/integration/test_example_reuse.py`, `dashboard/tests/guidance.spec.ts`, `docs/verification/milestone-4.md`. **Files to modify:** `service/queries/metrics.py`, `service/queries/history.py`, `dashboard/components/ExchangeInspector.tsx`, `dashboard/components/CohortControls.tsx`.

**Interfaces:** `record_example_review` accepts authenticated human review, item/lineage, permitted use and evidence; returns an append-only review receipt in Task 14's `example_reviews` storage. Adds guidance/repair/reuse filters without changing task/result denominators; no additional migration is required.

- [ ] Define candidate versus approved prompt use, already-exposed evaluation candidates, linked variants, unrelated same-root tasks, synthetic provenance, conflicting repair labels and mixed-manifest work-item grouping.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_example_reuse.py -q` and `npm run test:e2e -- guidance.spec.ts`; confirm absent reuse checks and inspector states fail.
- [ ] Implement manual operator review recording, exposure checks and compact Guidance inspector/filter additions. Retain human identity, review IDs, hashes, source lineage and independent validation evidence. No training/export pipeline, auto-promotion or learned gate generation is introduced.
- [ ] Rerun reuse, metrics and browser tests. Acceptance cannot approve reuse; evaluation-only variants cannot become supplied examples; exposed cases cannot be labelled unseen evaluation. Work items with differing actual guidance selections count once as mixed.

**Milestone 4 exit checks**

- [ ] Two Claude executions are independently visible and obey shared global/per-root limits, with Qwen capacity held separately.
- [ ] Reviewed reload, guidance withdrawal, required-context gates and example-use checks pass across queue boundaries.
- [ ] The inspector explains guidance supplied/excluded, gate reasons, paid repair evidence and reuse status without a new dashboard page.
- [ ] Record test evidence and the exact active policy/model profiles in `docs/verification/milestone-4.md`.

## Milestone 5 — Recovery and controlled adoption

### Task 16 — Validate process failure, uncertainty and fallback races

**Files to create:** `tests/recovery/test_process_loss.py`, `tests/recovery/test_fallback_races.py`, `tests/recovery/test_restart.py`, `tests/recovery/test_cancellation.py`. **Files to modify:** `bridge/runtime/reconciliation.py`, `scripts/reconcile-execution.py`, `bridge/runtime/ownership.py`, `bridge/runtime/fallback.py` and `service/queries/snapshot.py` only as required by demonstrated failures.

**Interfaces:** Hardens Task 4's existing `reconcile_execution` and recovery state under fault injection. Preserve its evidence/provenance requirements and confirmed live/terminated/uncertain outcomes. It never treats a resident-model list or stale heartbeat as proof of individual request termination.

- [ ] Define fault injection at reservation, external dispatch, acknowledgement, fallback commit, result delivery and completion recording. Include actual separate test-process termination, database disconnection, cancellation and reconnect.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/recovery -q`; new recovery behavior must first fail against its stated invariant.
- [ ] Harden reconciliation paths and recovery records using the existing durable state machine. Do not introduce inference replay, forced uncertainty clearance or a second scheduler. Verify the dashboard already exposes held uncertain capacity and explicit fallback terminal/unknown states throughout each injected failure.
- [ ] Rerun the fault matrix, including both race winners, with fixture-controlled schedules and project-owned processes. No duplicate local/fallback dispatch, fabricated disposition, premature slot release, recursive fallback chain or silent final resolution is permitted.

### Task 17 — Exercise capture outages, retention and bounded queries

**Files to create:** `service/maintenance/retention.py`, `tests/recovery/test_capture_outage.py`, `tests/integration/test_retention.py`, `tests/integration/test_query_bounds.py`, `tests/pilot/test_operational_load.py`. **Files to modify:** `bridge/telemetry/spool.py`, `service/http/stream.py`, `service/queries/history.py` and `dashboard/lib/events.ts` where checks expose gaps.

**Interfaces:** Retention uses configured policy and produces content tombstones/replay-boundary facts. Expand Task 5's existing capture-outage, overflow/gap and admission safeguards through recovery/load tests; they are not first delivered here. Capture recovery preserves original event IDs and never invokes inference. Queries remain paginated and omit body payloads until requested.

- [ ] Define collector outage, spool exhaustion, reconnect duplicates, expired SSE cursor, large/Unicode bodies, deletion during viewing and sustained synthetic traffic at configured capacity.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/recovery/test_capture_outage.py tests/integration/test_retention.py tests/integration/test_query_bounds.py -q`; confirm missing recovery/retention behavior fails.
- [ ] Implement retention tombstones and harden existing capture-gap/replay handling and bounded processing where these tests expose failures. Never silently truncate complete content, make deletion appear reproducible, or rerun a model because capture failed.
- [ ] Rerun tests and `tests/pilot/test_operational_load.py` with synthetic data only. Record observed event-to-display latency, spool growth, query latency and payload sizes against the configured workload. Set adoption thresholds from these measurements with the user; do not claim an unmeasured throughput/SLA.

### Task 18 — Prepare reversible operation and run a controlled pilot

**Files to create:** `scripts/start-monitor.ps1`, `scripts/stop-monitor.ps1`, `scripts/backup-monitor.ps1`, `docs/operations/runbook.md`, `docs/operations/pilot-checklist.md`, `docs/verification/milestone-5.md`. **Files to modify:** `scripts/verify-paths.ps1` and `config/pilot-policy.example.toml` for verified deployment facts only.

**Interfaces:** Operator commands start/stop only recorded project-owned processes, use explicit project-local paths and secret references, and refuse targets outside this project. Stop drains/cancels with recorded outcomes; it is not a recursive filesystem cleanup command.

- [ ] Prepare the runbook for configuration validation, approved reload, start/stop, backup/restore, uncertain-execution reconciliation, capture outages and rollback to use of the unchanged old bridge. Record required model/client capabilities and full-content access controls.
- [ ] Verify path/process guards against an outside-project target and verify backup restore into a separate project test instance. Run the relevant Python suites and dashboard type/build/browser checks once against the final candidate.
- [ ] After separate authorization for live execution, run a limited read-only workload through the new bridge using the temporary project-local client configuration. Start with one paid worker, then the configured two-worker setting. Compare captured exchanges, outcomes and operator observations; record counts and unknowns rather than asserting savings.
- [ ] Review the pilot evidence and proposed switch/rollback commands with the user. Broader routing or registration changes require separate authorization. If client registration cannot be isolated without changing an existing configuration, stop that adoption step and present the exact proposed change; do not modify the existing configuration implicitly.

**Milestone 5 exit checks**

- [ ] All specification acceptance criteria map to passing evidence, with actual adapter limits and unobserved boundaries documented.
- [ ] Restart/cancellation/timeout/replay tests preserve ownership, content and statistics; no model call is duplicated by telemetry recovery.
- [ ] A limited authorized live pilot demonstrates the whole flow and paid fallback while preserving existing sources and registrations.
- [ ] Backup/restore, stop/restart and rollback instructions work on project-owned instances. No global cutover is claimed or performed by completing this plan.

## Specification coverage

| Specification area | Owning tasks |
|---|---|
| Source isolation, scope and architecture, sections 1–3 | 1, 5, 9, 18 |
| Identities, events, content, sections 4–6 | 1, 2, 5, 8, 17 |
| Dispositions, example exchange and lineage, sections 7–8 | 3, 6, 7 |
| Concurrency, fallback and recovery, section 9 | 4, 7, 12, 16 |
| Configuration and reload, section 10 | 1, 4, 13, 14 |
| PostgreSQL and statistics, sections 11–12 | 2, 3, 7, 8, 15 |
| Dashboard, section 13 | 9–11, 15 |
| Guidance, seed and review metadata, section 14 | 3, 7, 14, 15 |
| Acceptance criteria 1–4 | 8/10/12, 5/10, 6, 3 respectively |
| Acceptance criteria 5–8 | 8–9, 13, 4/12/16, 12 respectively |
| Acceptance criteria 9–11 | 2/5/8/10/17, 3/14/15, 1/5/18 respectively |
| Acceptance criteria 12–14 | 7/14, 1/4/13/14, 6/15 respectively |
| Acceptance criteria 15–17 | 7/16 and the Milestone 5 fault matrix |
| Review/execution boundary, section 16 | This plan and Task 18 |

## Review and execution handoff

The recommended execution approach is **native execution with milestone exit checks**, allowing synthetic dashboard work after Milestone 1 alongside Milestone 2 development, with an independent review of scheduler/fallback changes before live concurrency and a final integration review. The contracts are tightly connected; retaining one implementation context avoids repeated rediscovery while targeted review covers the highest-risk transitions. Delegated task execution remains an option if the user prefers it; no agents are dispatched by this plan.

Review this plan before implementation. The user has requested documentation only at this stage. No source scaffold, dependency installation, test run, live model invocation or registration change is implied by the unchecked tasks. Git initialization, staging, commits and pushes are not part of this authorization.

The plan defines measurable exit evidence, not estimated calendar dates. After the first live slice, actual adapter and test effort will support a more credible delivery estimate. Work should not be described as complete while its milestone's required live or recovery evidence is unavailable.

### Implementation references

- [FastAPI features](https://fastapi.tiangolo.com/features/) supports the proposed typed HTTP service and schema-driven boundary.
- [Psycopg 3 documentation](https://www.psycopg.org/psycopg3/docs/) is the reference for PostgreSQL access and transaction handling; pin a stable compatible release during implementation.
- [React Flow documentation](https://reactflow.dev/learn) is the reference for the grouped interactive flow visualization.
- [Next.js static export guide](https://nextjs.org/docs/app/guides/static-exports) supports serving the browser application as static output. Browser-only dashboard loading must still be configured explicitly; a client component declaration alone is not proof that runtime data avoids server rendering.
