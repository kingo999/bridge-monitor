# Parent Router Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in GPT-side router that audits every decision, returns the existing authoritative Sonnet result, and measures one independently verified Skynet/Qwen candidate without delivering it.

**Architecture:** Attribute calls and decisions by stage and execution mode in the existing event store before dispatching shadow work. `route_work` records a parent decision, uses current paid admission, and durably queues a separate shadow job after the paid result; a dedicated worker uses existing Qwen leases and the isolated Docker verifier. Queries and the dashboard compare both arms on one work item while excluding shadow activity from production metrics.

**Tech Stack:** Python 3.13+, Pydantic, Psycopg 3, PostgreSQL, pytest, MCP; Next.js 16, TypeScript, Playwright; existing Skynet agent and digest-pinned Docker verifier.

**Spec:** [GPT-side router design](../../design/parent-router-2026-10-18.md).

**Implementation record:** Tasks 1–5 were committed as `b5cd3c7`, `0bb671b`,
`6e37343`, `217a155`, and `17ac8ae`. Task 6 and the final review corrections are
documented in [the verification record](../../verification/parent-router-shadow-2026-10-18.md).
The operational shadow policy and worker remain inactive pending a separate cutover review.

## Global Constraints

- All new work stays under `C:\AI\bridge-monitor`; do not change `C:\AI\gpt-claude-mcp-bridge-v2` or unrelated dirty files.
- Phase one is explicit `route_work` shadow mode only. Sonnet is authoritative; Qwen is never delivered, and no second paid call is launched for comparison.
- Preserve current `delegate_claude`, Claude-to-Qwen behavior, paid admission, parent acknowledgements, timeout fallback, held-uncertain capacity, and the existing definition of production “not routed.”
- One task/work item represents the parent request; each arm has separate call, attempt, model execution, result, decision and evaluation identities. Do not make a shadow child work item: the current leaf-cohort query would drop the parent.
- Reviewed configuration owns adjustable limits and sampling. Pin policy, prompt, model/digest, task and verifier versions at admission; recheck withdrawn verifier/safety constraints before dispatch. No configuration bypass of independent verification or the ban on local delivery.
- Store exact prompts/responses and hashes through existing protected content storage. Keep unknown usage, unverifiable quality, and billed cost unknown; parent-reported judgments remain labeled as reports.
- Database tests use only `BRIDGE_MONITOR_TEST_DSN` containing `bridge_monitor_test`. Do not run migrations or synthetic fixtures against the operational database.
- The existing uncommitted `config/code-fix-natural-shadow.toml` correction is separate. Freeze a reviewed prompt/config hash before the first natural case; never silently mix protocols.

**Suggested execution model: Claude Opus.** This changes result-delivery and metric trust boundaries across asynchronous workers; advisory only, and the user's chosen model remains valid.

## File map and interfaces

| File | Responsibility |
| --- | --- |
| `database/migrations/006_router_shadow.sql`, `contracts/events.py`, `service/ingest/projector.py` | Durable `execution_mode` on calls/jobs and `decision_stage`/mode on decisions; project the existing event chain without guessing stage for historical rows. |
| `contracts/policy.py`, `bridge/runtime/admission.py`, `bridge/runtime/ownership.py`, `bridge/runtime/completions.py` | Validate a disabled-by-default router section in the reviewed active policy, pin shadow admission, claim only the intended mode, and retain an undelivered Qwen result without creating a Claude consumer obligation. |
| `service/queries/metrics.py`, `service/queries/snapshot.py`, `service/queries/history.py` | Filter production routing and expose attributed router history; increment metric definition version. |
| `contracts/router.py`, `bridge/router/eligibility.py`, `bridge/router/cases.py`, `config/parent-router.example.toml` | Strict request/config shapes, deterministic eligibility, and hash-pinned pre-solution verifier bundles. |
| `bridge/router/ingress.py`, `bridge/mcp_server.py` | Parent decision and `route_work` tool; existing paid coordinator remains the authoritative execution path. |
| `bridge/router/shadow_worker.py`, `bridge/startup.py`, `scripts/serve-router-shadow.py` | Dedicated bounded queue drainer with project-local runtime setup; reuse Qwen lease/agent and `DockerVerifier`, never resend an uncertain job. |
| `service/queries/router.py`, `service/http/query.py`, `dashboard/components/RouterAudit.tsx`, `dashboard/components/ShadowCohort.tsx` | Task audit and filterable shadow report; authenticated body drilldown via the existing content endpoint. |
| `docs/operations/parent-router-shadow.md`, `docs/verification/parent-router-shadow-2026-10-18.md` | Launch, recovery, cohort registration and evidence/exit receipts. |

Use `Literal["production", "shadow"]` for call/job mode and `Literal["parent_router", "claude_child", "legacy"]` for decision stage. Existing records migrate to `production` and `legacy`; do not relabel them as known parent or child decisions. `decide_parent_route(task: TaskPacket, prompt: str, output_contract: str, verifier_ref: str | None, policy: RouterPolicy, capture: CaptureFacts) -> RouterDecision` returns `eligible: bool`, `reason: str`, and pinned manifest. `register_case(project_root: Path, case: RouterCase, policy: RouterPolicy) -> CaseReceipt` writes a content-addressed immutable snapshot; `load_case(project_root: Path, verifier_ref: str) -> RouterCase` verifies all hashes. `BridgeCoordinator.route_work(task: TaskPacket, prompt: str, *, requested_model: str, reserve_tokens: int, output_contract: str, verifier_ref: str | None = None, source_id: str | None = None) -> dict` returns the existing paid outcome plus `router_decision_id`, `shadow_call_id | None`, and `shadow_state`; shadow status is separate from paid status. The shadow worker's `run_one_shadow(coordinator: BridgeCoordinator, verifier: DockerVerifier) -> dict | None` claims only a queued shadow call and appends an evaluation; it never returns an answer to MCP.

## Review Focus

1. A shadow dispatch on the same observed work item must not change production routed, not-routed, takeover, worker or acceptance numerators. Task 1 tests the before/after metrics and dashboard snapshot.
2. A paid result must return even if shadow enqueue, worker startup, Qwen, Docker, or later audit capture fails; the failure is recorded and never causes a comparison paid call. Tasks 3–4 test each boundary.
3. A restarted shadow worker must not relaunch an accepted or uncertain Qwen job; capacity remains held until existing reconciliation proves release. Task 4 tests process-loss and uncertain status.
4. A verifier bundle that changed after registration, has passing baseline, arrived after a candidate answer, or was withdrawn must not make a task eligible. Task 2 and Task 4 test registration and dispatch checks.
5. A Qwen answer with valid JSON but wrong code, a passing answer with incomplete capture, or an incomparable paid answer must not appear as delivered or as proven savings. Tasks 4–5 test result, evaluation and report semantics.

---

### Task 1: Attribute shadow records and protect production accounting

**Files:** Create `database/migrations/006_router_shadow.sql`; modify `contracts/events.py`, `service/ingest/projector.py`, `service/queries/metrics.py`, `service/queries/snapshot.py`, `service/queries/history.py`; extend `tests/integration/test_metrics.py`, `tests/integration/test_history.py`, `tests/integration/test_snapshot.py` (create if absent).

**Interfaces:** Persist `calls.execution_mode`, `runtime_jobs.execution_mode`, `delegation_decisions.decision_stage` and `delegation_decisions.execution_mode` with checked values. Project `CALL_RECEIVED.payload.execution_mode` and `DELEGATION_DECIDED.payload.decision_stage/execution_mode`; missing fields mean historical `production`/`legacy`. Every production metric and snapshot route predicate joins or filters attributed production calls/decisions; shadow-only comparison data remains queryable. Preserve existing API fields and bump `DEFINITION_VERSION` to `phase1-v3`.

- [ ] **Step 1: Write failing database tests.** Seed one observed paid work item, then a same-work-item shadow decision, Qwen dispatch and undelivered result. Assert production routed/not-routed/pending, takeover, worker counts and stage denominators are unchanged; shadow-only calls are not counted as delivered; legacy fixtures retain their previous result. Assert an explicit production paid decision with no production local dispatch is “not routed,” while a production local dispatch followed by paid fallback stays routed. Check an as-of timestamp before/after shadow events.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/integration/test_metrics.py tests/integration/test_history.py tests/integration/test_snapshot.py -q` with the dedicated test DSN; expect new mode/stage assertions to fail.
- [ ] **Step 3: Add the migration, projector and query predicates.** Reject unknown mode/stage values, avoid a new work item, and ensure historical records stay identifiable as `legacy` rather than inferred parent decisions. Include shadow mode on dispatch payload for event-only readers, with the call row as query authority.
- [ ] **Step 4: Run green tests.** Repeat the focused command; expect all seeded production metrics and history tests to pass.
- [ ] **Step 5: Commit only Task 1 files.** Message: `bridge-monitor: isolate shadow routing in audit queries`.

### Task 2: Register natural verifier bundles and decide eligibility

**Files:** Create `contracts/router.py`, `bridge/router/eligibility.py`, `bridge/router/cases.py`, `config/parent-router.example.toml`, `tests/router/test_eligibility.py`, `tests/router/test_cases.py`; modify `contracts/policy.py`, `tests/contracts/test_policy.py`; reuse `bridge/code_fix_verifier.py` without weakening isolation.

**Interfaces:** Produce the `RouterPolicy`, `RouterCase`, `CaseReceipt`, `CaptureFacts`, `RouterDecision`, `register_case(...)`, `load_case(...)`, and `decide_parent_route(...)` interfaces from the file map. Add `router: RouterPolicy` with disabled default to both `PolicyInput` and `PolicySnapshot` so existing active policy remains valid and future shadow activation uses the existing config-version hash/review path. The case contains task/origin reference, capture timestamp, complete baseline source, trusted tests, output contract, and source/test hashes; the reference solution is absent at registration. Router policy permits only `disabled`/`shadow` and contains category/size/context, queue/execution, token, sample, verifier, withdrawal and retention limits. Eligibility accepts only `single_file_python_fix` plus `complete_python_module_v1`, a failing baseline, trusted pre-solution tests and a valid local profile; uncertain facts or insufficient budget return a stable paid reason.

- [ ] **Step 1: Write failing unit/verifier tests.** Assert valid pre-solution registration and hash-stable reload; reject missing origin/time, changed files/hashes, a baseline that already passes, oversized context, unsupported category/contract, expired/withdrawn verifier, absent profile, capture degradation and no spare call/token budget. Assert the prompt given to Qwen contains broken source but no expected outputs or later Sonnet answer.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/router/test_eligibility.py tests/router/test_cases.py -q`; expect missing interfaces and decisions.
- [ ] **Step 3: Implement strict contracts and content-addressed registration.** Use the current digest-pinned Docker verifier for baseline checks; keep case files under `var/router/cases`, atomically create them, and reject a changed digest rather than updating a registered case. Hash the effective prompt/config/model/verifier manifest; keep sensitive test content out of decision facts.
- [ ] **Step 4: Run green tests.** Repeat the focused command plus `./.venv/Scripts/python.exe -m pytest tests/contracts/test_policy.py tests/shadow/test_verifier.py -q`; expect deterministic reasons, unchanged active-policy compatibility and unchanged verifier isolation.
- [ ] **Step 5: Commit Task 2 code, tests and example config.** Message: `bridge-monitor: pin router cases and eligibility`.

### Task 3: Add opt-in parent routing with one paid result

**Files:** Create `bridge/router/ingress.py`, `tests/router/test_ingress.py`; modify `bridge/mcp_server.py`, `bridge/adapters/codex_ingress.py`, `bridge/runtime/admission.py`; extend `tests/adapters/test_ingress.py`, `tests/agent/test_jobs.py`.

**Interfaces:** Produce `BridgeCoordinator.route_work(...)` and parent-only MCP `route_work` using the file-map signature. Persist one `parent_router` production decision and original prompt before paid admission. Reuse `delegate_claude`'s scope/model/source checks and paid execution without a second paid call. If eligible and the paid result is delivered, enqueue at most one `shadow` Qwen job with a stable idempotency key, pinned manifest and same work item; return its ticket without waiting for Qwen. If enqueue fails, append a shadow failure/skip record and return the paid outcome. Do not enqueue after a rejected or undelivered paid call; do not silently convert an ambiguous paid outcome into another paid call.

- [ ] **Step 1: Write failing MCP/DB tests.** Assert parent audience exposes `route_work`, Claude audience does not; invalid model/source/budget cannot bypass existing admission; original prompt and decision precede paid dispatch; eligible requests return one paid result and one queued shadow ticket; ineligible requests record reason and zero Qwen jobs. Replayed request returns the same decision/job, and queue/capture failure preserves the paid answer with an auditable shadow state.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/router/test_ingress.py tests/adapters/test_ingress.py tests/agent/test_jobs.py -q` against the test DSN; expect missing tool/queue behavior.
- [ ] **Step 3: Implement the parent endpoint and durable enqueue.** Pin active policy and case manifest once; require spare experiment allowance within existing root budget, allocate the Qwen job through the shared arbiter, and record parent/router and shadow/local decisions with distinct IDs. Keep the current paid response contract intact and the Qwen response inaccessible through this endpoint.
- [ ] **Step 4: Run green tests.** Repeat the focused command and existing parent delegation/child delegation tests; expect no duplicate paid or local dispatch.
- [ ] **Step 5: Commit only Task 3 files.** Message: `bridge-monitor: add opt-in shadow router ingress`.

### Task 4: Execute and independently verify queued shadow work

**Files:** Create `bridge/router/shadow_worker.py`, `scripts/serve-router-shadow.py`, `tests/router/test_shadow_worker.py`; modify `bridge/startup.py`, `bridge/runtime/ownership.py`, `bridge/runtime/completions.py`, `bridge/mcp_server.py`; extend `tests/agent/test_jobs.py`, `tests/integration/test_dispositions.py`, `tests/integration/test_policy_startup.py`.

**Interfaces:** `run_one_shadow(coordinator: BridgeCoordinator, verifier: DockerVerifier) -> dict | None` selects only `runtime_jobs.execution_mode='shadow'`, using existing Qwen capacity/lease/agent, status polling and cancellation rules. Persist raw Qwen content in `results.raw_content_id` and parsed candidate content as a `result_parts` reference, with `results.delivered_at=NULL`, no `result.delivered` event or Claude obligation; append one independent evaluation with verifier version, parse/import/test/resource reason and elapsed time. `build_shadow_coordinator(runtime_config_path: str | Path) -> BridgeCoordinator` in `bridge/startup.py` builds a project-local Qwen-only coordinator with a separate durable spool. `serve-router-shadow.py --config <path>` is a bounded, restartable loop using reviewed queue and concurrency settings; it never replays an accepted/uncertain request.

- [ ] **Step 1: Write failing fake-agent and DB tests.** Assert production Qwen jobs are never claimed by the shadow worker and vice versa; a passing candidate remains undelivered but has evaluation evidence; valid JSON/wrong code, malformed/oversize output, unavailable Docker, timeout, cancellation, capture gap and withdrawn case all produce distinct failure/unknown records. Simulate restart after launch claim or ambiguous remote acceptance and assert no second dispatch, held capacity and explicit reconciliation requirement.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/router/test_shadow_worker.py tests/agent/test_jobs.py tests/integration/test_dispositions.py -q` against the test DSN; expect missing mode-specific completion/worker path.
- [ ] **Step 3: Implement the mode-specific worker path.** Recheck current withdrawal/safety gate, use the pinned case/prompt/model digest, mark dispatched before inference, invoke the existing Skynet adapter once, parse the complete-module contract, and run only the isolated verifier. Keep production fallback behavior unchanged; shadow failure never initiates Sonnet. Commit result, evaluation and terminal job state atomically where possible, with idempotent retry of persistence only.
- [ ] **Step 4: Run green tests.** Repeat the focused command; then run `./.venv/Scripts/python.exe -m pytest tests/router tests/shadow tests/agent tests/integration -q` and expect no production regressions.
- [ ] **Step 5: Commit only Task 4 files.** Message: `bridge-monitor: verify undelivered shadow candidates`.

### Task 5: Expose a per-task audit and separate shadow cohort

**Files:** Create `service/queries/router.py`, `dashboard/components/RouterAudit.tsx`, `dashboard/components/ShadowCohort.tsx`, `tests/integration/test_router_queries.py`, `dashboard/tests/router-shadow.spec.ts`; modify `service/http/query.py`, `service/queries/history.py`, `dashboard/components/ExchangeInspector.tsx`, `dashboard/components/DashboardClient.tsx`, `dashboard/lib/api.ts`, `dashboard/lib/contracts.generated.ts` (regenerate by the existing contract script if applicable).

**Interfaces:** `read_router_audit(dsn: str, work_item_id: str) -> dict` returns ordered parent/child decisions, pinned manifests, queue/dispatch states, original/effective prompt and result content IDs, verifier evidence, paid/parent dispositions and corrections. `query_shadow_cohort(dsn: str, filters: RouterCohortFilter) -> dict` returns attempted-task denominator, result/independent-evaluation coverage, reason groups, model/prompt/verifier versions, latency stages, provider-specific usage coverage and cost basis. Never infer net billed savings; show only a conditional avoided-paid-worker-call estimate, excluding incomparable or incomplete pairs.

- [ ] **Step 1: Write failing query/browser tests.** Seed eligible pass, wrong, timeout, paid-direct, incomplete capture, superseded evaluation and paid answer outside the same contract. Assert reasons and denominator include every attempt, latest corrections are separately visible, secrets are absent from list responses, exact bodies load only through authenticated content drilldown, production cards are unchanged, and billed savings/unknown tokens render as unknown.
- [ ] **Step 2: Run red tests.** `./.venv/Scripts/python.exe -m pytest tests/integration/test_router_queries.py -q`; from `dashboard`, run `npm run test:e2e -- router-shadow.spec.ts`; expect missing report/UI behavior.
- [ ] **Step 3: Implement attributed queries and compact dashboard panels.** Put router coverage beside existing traffic coverage; add an expandable per-task timeline and filterable shadow cohort, retaining existing side-by-side response comparison. Separate independent verifier outcome, Claude/GPT reports, provider usage, list estimates, subscription allowance and billed charges.
- [ ] **Step 4: Run green checks.** Repeat focused commands; run `npm run typecheck`, `npm run build`, `npm run test:e2e` in `dashboard`; expect passing UI checks and no changed production card semantics.
- [ ] **Step 5: Commit only Task 5 files.** Message: `bridge-monitor: show router audit and shadow cohort`.

### Task 6: Operational proof and natural-cohort handoff

**Files:** Create `docs/operations/parent-router-shadow.md`, `docs/verification/parent-router-shadow-2026-10-18.md`; modify only project-local launcher/config documentation required for the dedicated worker. Add a project-local integration fixture only if the existing test harness cannot express the end-to-end check.

**Interfaces:** Do not activate an unreviewed router policy or live canary. Document an explicit activation/soft-reset and rollback path for the disabled-to-shadow setting, queue-worker process ownership and uncertain-job reconciliation. Register qualifying natural fixes at arrival using Task 2's pre-solution snapshot contract; a 30-case report is a future review trigger, not an implementation exit prerequisite when no natural tasks have arrived.

- [ ] **Step 1: Run one isolated end-to-end shadow proof against the test database.** Use a controlled pre-solution fix and fake agent/isolated verifier: one paid delivery, one undelivered Qwen result, preserved exact bodies and hashes, independent evaluation, unchanged production metrics and separate audit. Record both success and timeout/uncertain paths.
- [ ] **Step 2: Verify deployment and recovery procedures without cutover.** Check worker starts disabled, no shadow claim without reviewed policy, config reload affects only later admissions, shutdown leaves uncertain capacity held, and a restart does not replay remote work. Record the active operational policy hash and ensure it remains unchanged unless separately reviewed.
- [ ] **Step 3: Run final checks.** `./.venv/Scripts/python.exe -m pytest -q` under the dedicated test DSN; from `dashboard`, `npm run typecheck`, `npm run build`, `npm run test:e2e`. Check `git diff -- gpt-claude-mcp-bridge-v2` and `git diff --cached -- gpt-claude-mcp-bridge-v2` are empty. Record actual skips, any unavailable Skynet/Docker dependency, and natural cohort count without inventing cases.
- [ ] **Step 4: Write and commit the evidence/runbook.** Message: `bridge-monitor: document shadow router proof and recovery`. Stop before any live Qwen-result delivery or automatic gate update; assess canary only after 30 reconciled natural attempts and separate review.
