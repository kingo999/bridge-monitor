# Design Mode

## Native dashboard tab, 2026-11-04

Design workflows is now a native main dashboard tab between Codex/Claude and Audit. It reuses the existing operator-authenticated list/detail API and shared panel, filter and table styles. Project/awaiting-decision filters and selected evidence persist across main-tab switches. Refresh runs only while the tab and browser document are active, with aborted requests preventing stale detail replies. The standalone read-only page remains available at its existing endpoint. Workflow actions continue through the owning chat. Native-tab and existing standalone/Audit/efficiency browser checks passed (11 tests), with a production build and TypeScript check.


Operating guide: [docs/operations/design-mode.md](../docs/operations/design-mode.md).

## Decisions

- Approved 2026-11-05: before saving a plan, record whether work can be split
  safely. The initial execution policy permits at most two independent read-only
  workers per parent task, keeps writes under a shared repository lease sequential,
  and requires the controlling Codex session to verify both results and their
  combined outcome. Recorded in the Codex rules and Design Mode operating guide.
  Design Mode retains its existing single formal review per iteration.
- Opt-in (`opt_in=true`) read-only planning loop: Chat → Codex (planner) → Claude (review via
  `route_work`, `deep_review`, `auto`) → Chat (accept, revise or reject after a delivery receipt).
- Parent MCP audience only, with a single operator owner derived by the host
  (`producer_id@OS account`). The Claude child inventory is unchanged.
- Ownership, review, worker model and write scope are controlled by Bridge. Evidence is bounded bridge reads
  (`read_file`, `find_files`, `search_text`, `git_status`). This is bridge policy, not native or OS
  read confinement.
- Optional `chat_model` and `chat_effort` on submitted briefs are display metadata for the last handoff.
- Durable and idempotent: request keys, `expected_version` concurrency, immutable artifacts,
  evidence, deliveries and audit. Closed-chat recovery = `design_mode_retrieve`, then
  `design_mode_confirm_delivery` with the package sha256.
- Revise has two forms. With only a reason it gives `paused` / actor `chat` /
  `status_reason=revision_requested` on the same iteration, with a `revise_requested` audit and the
  label "Awaiting revised brief"; resume is refused. Revise with brief + new key against the
  current version creates N+1, and the combined path still works. Accept, reject and brief-less
  revise retries replay without new audit and conflict when contradictory. Request keys replay
  only for the exact N→N+1 revise (same brief and reason); initial keys and revise keys never
  cross over. Retry never runs automatically, and a terminal `failed` needs a new submit.
  Accept/reject/revise counts come from `dm_audit`.
- Review identity: hop `worker_call_id` = predicted `cc-call:<work_item>` and is never overwritten.
  `BridgeCoordinator._submit_claude` locks the owner `dm_workflows` row `FOR UPDATE` across
  `ingress.submit` for `design-mode:` tasks and admits only the active original claim; other
  tasks go straight to `ingress.submit`. A mismatched outcome call ID gives `review_unknown`
  (`review_call_id_mismatch`). Reconcile needs `pool='claude'` and the iteration's
  task/work item. Cancel either fences before admission or calls `request_cancel_tree` on the
  admitted job, and always reports `worker_termination_confirmed=false`.
- Monitor is read-only GET under the operator session: metadata and versioned artifact
  refs only, never bodies. It has a project filter, outcome labels (Ready for Chat / Accepted
  on iteration N / Rejected on iteration N) and a hop timeline.
- The dashboard links to it with a plain href, "Design workflows (read-only)", on the Codex / Claude tab.
- Monitor "Awaiting my decision" filter (accepted design, workflow `10ea7f5e-4dd4-594a-aef5-7b6abb158020`
  v5, plus the independent review's items 1–4). It is an optional `[dashboard] design_mode_owner_id`, an
  exact owner_id copied from the detail view. It is a display preference, not authorization.
  Validation is in `validate_design_mode_owner_id`, called by both `create_app` and `query_router`:
  None/omitted disables the filter, and an empty, whitespace or non-string value is a startup
  `ValueError`. The launcher only does `dashboard.get`, and no personal owner appears in the example
  config. The filter matches `w.owner_id=%s AND w.status='delivered'` in SQL before
  `ORDER BY updated_at DESC LIMIT`, ANDed with the project filter. Project options stay unfiltered.
  There is no schema, index, routing or permission change. Missing schema returns the exact legacy
  response. Otherwise `decision_filter_configured` is always present, and an unbound filtered
  request returns no workflows and runs no workflow query. The page is the inline `_DESIGN_MODE_PAGE`
  (no Next build), with a native checkbox, an `aria-describedby` help text that names the
  stale/review_unknown/paused exclusions, and a separate `#filter-status` (role=status, aria-live)
  that is set only from successful responses. The default URL is unchanged, and
  `awaiting_my_decision=true` is added only when checked.

## Current state (2026-11-02)

- Implemented: core and tools in `bridge/mcp_server.py`; API, page, project filter, outcome
  labels and counts in `service/http/query.py`; dashboard link in `dashboard/components/DashboardClient.tsx`.
- Corrections (2026-11-02): no inline DDL. `migrate_design_mode` wraps `database.migrate.migrate`:
  it returns False when the ledger already has 013, and otherwise needs the canonical
  `database/migrations/013_design_mode.sql` (operator handoff pending) or raises. A plan needs a
  bounded evidence read or empty-project evidence (a full `find_files` with no results). Drift
  marks the workflow stale but keeps the plan. Staleness rechecks use `read_file`. Accept rechecks
  the source first. Resubmitting an identical plan replays the result; a changed plan conflicts.
  `retrieve` rechecks project authorization and counts sequence-3 attempts in `dm_audit`
  (`package_retrieved`). `confirm_delivery` needs a retrieval with a matching digest.
- Verified (2026-11-02, items 4+5 done): real test DB, worker mocked, not live integration.
  `tests/integration/test_design_mode.py` went 33 passed (run `8574d12f-87a4-4742-86d4-50f64f0a710a`).
  New tests: revise-without-brief/resubmit, accept/reject replay, fenced cancelled claim,
  exact-claim admission guard, cancel-during-admission row lock, unrelated/misattributed worker,
  and genuine-stage restart reconciliation. The admission tests replace `ingress.submit` with a fake
  that admits through the real `admit_call`. No red run was recorded for items 4+5: production
  edits preceded the tests. Regression (parent_acknowledgements, direct_execution, ownership,
  dispositions, admission): 30 passed (run `2da21b87-46c6-4b73-8bb3-f73b8b0a513b`). Codex/Claude
  efficiency (+db), router ingress and presets: 41 passed (run `ffd3a67d-b51e-4e60-ab2f-96939eab480a`).
  Dashboard build/typecheck passed separately; the browser smoke test has not been run.
- Packet binding (2026-11-02): for `design-mode:` tasks only, both checks rebuild the expected
  read-only TaskPacket and prompt from the saved iteration via `_review_packet`.
  (1) `route_work` entry, through `DesignMode.route_refusal`, runs before any routing decision is
  recorded or reserved, on either branch. It needs the owner's current claim, an exact task and
  prompt, `output_contract=design_mode_review_v1`, no `verifier_ref` or `source_id`, the paid
  fallback model and `REVIEW_RESERVE_TOKENS`.
  (2) `_submit_claude` calls `DesignMode.admission_refusal` while holding the same
  `FOR UPDATE OF w` lock. It needs the active claim, the original `call_id` and work item, and
  `parent_call_id=gpt:<task_id>`. It strips only `repository_binding` before comparing the full
  task and prompt.
  Refusals return `design_mode_claim_inactive` or `design_mode_packet_mismatch`. A matching retry
  passes; ordinary tasks are unchanged.
  Red before the fix (run `cff5505d-a2e5-4995-85f5-e02def02fbc3`): 2 failed, 33 passed.
  After the fix: `test_design_mode.py` 35 passed (run `95233806-1e9e-4fe3-83ac-de950f98cc6c`).
  Regressions: `test_router_queries.py` 5 passed (run `2e68156d-75bc-42b3-8620-e5df7cb122ce`);
  `test_ownership.py` 2 passed (run `a2321877-f212-4a36-91a4-a814ae7ec9bd`). Worker mocked;
  not live integration.
- Browser smoke (2026-11-02, NOT passing): `dashboard/tests/design-workflow-page.spec.ts` now selects
  the installed system Edge (`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`) via
  `test.use({launchOptions:{executablePath}})` only on win32 with `BRIDGE_MONITOR_PLAYWRIGHT_CHANNEL=msedge`
  and when the file exists. Run `113a4938-746b-40b2-b4ae-0133ba6ada77` (`bridge-selftest`,
  `dashboard-e2e`, offline): 1 failed. Edge launched (pid) but exited before the test logic ran:
  `PathService key 112: An attempt was made to reference a token that does not exist (0x3F0)` and
  `DevTools remote debugging requires a non-default data directory` despite Playwright's
  `--user-data-dir`. This is a lease/host Edge launch problem, not a fixture or selector mismatch; page
  assertions have not run. This test is authenticated HTML with route-mocked fixture data, never a live provider run.
- Per-iteration review budget (2026-11-02). Root cause: every iteration reused task
  `design-mode:<wid>`, and `runtime_task_budgets` is keyed by task (`call_budget=1`, 60000 reserve).
  A user-authorized revision was therefore refused as `root_budget_exhausted` at its real second
  admission.
  Fix (`bridge/mcp_server.py` only): `DesignMode._iteration_task_id`. Iteration 1 keeps the legacy
  `design-mode:<wid>`; N≥2 gets `design-mode:<wid>:iteration:<N>`, with the same durable
  `workflow_id` and the existing per-iteration `work_item_id`. `_claim` now selects on the current
  iteration's exact `i.task_id` + `i.work_item_id` + `w.owner_id` instead of parsing the ID suffix. The
  `FOR UPDATE OF w` lock, frozen-packet comparison, cancel and reconcile are unchanged; cancel and
  reconcile already read the durable iteration row. Shared admission/budget code, routes, settings and
  the project filter are unchanged. `cc_reservations.workflow_id` now carries the per-iteration task ID;
  it is a label, not a cap.
  Legacy rows keep their stored IDs, with no refund.
  Red (run `1c0953cf-f471-47c3-804f-2fcaf204a687`): 2 failed with real `root_budget_exhausted`, 36 passed.
  Green: `test_design_mode.py` 38 passed (run `20cd80d6-3a37-4308-af49-bfd7e5e18f28`).
  New tests: two budgets on one workflow, exactly two jobs/debits after replay and retrieval; stale,
  spoofed, foreign-owner and packet-mismatch refusals plus cancel on iteration 2; and the legacy
  iteration 2 is retrievable and not refunded. They use the real guard + real `admit_call` with an
  ingress stand-in (`claude_waiting=2`); no worker, not live integration.
  Regressions all passed: router_queries 5 (`231a72f9-…`), ownership 2 (`a865c553-…`), admission 3
  (`aff1bfc9-…`), parent_acknowledgements 14 (`9c1c2b71-…`), codex_claude_efficiency_db 10
  (`cabe7273-…`), dispositions 3 (`4e83abb8-…`).
  Broad backend-integration (`9ce3bd20-d2cf-43c7-8457-258c15f9ff6e`): 311 passed, 13 failed, all in
  `test_validation_export.py` (usage `invalid_observation`); those failures reproduce alone
  (`4ba8be24-82b5-4f08-b7cc-dc2050c042c6`). They are outside the Design Mode path, but no pre-change
  baseline was run.
- Prior-review overlap guard (2026-11-02). Independent review `55a33c5a-…` found that revise from
  `review_unknown` could fund iteration N+1 while N's job was still running.
  Fix (`bridge/mcp_server.py`, `DesignMode._prior_review_live` + one check in `decide`): every
  resubmit that creates N+1 (combined, paused→resubmit, stale) runs under the existing owner
  advisory lock and `FOR UPDATE` workflow row. Before `_new_iteration` it looks up the iteration's
  hop-2 claim and its exact job (`call_id`, `pool='claude'`, `task_id`, `work_item_id`). The request
  returns `conflict`/`prior_review_active` with no state, version, audit, iteration or budget change
  unless the job is missing or is `completed`/`terminated`/`cancelled` with `slot_held=false`. A
  finished state with a held slot is uncertain and fails closed.
  A missing job may advance because admission holds the same row lock and needs `review_claimed`
  plus the current iteration identity, so the old claim is refused (`design_mode_claim_inactive`),
  as tested. Brief-less revise still pauses with no dispatch.
  Red (run `8ae4d6af-a66a-44ad-a17a-1bff4f1e9701`): 7 failed, 42 passed. The failures were 5
  live-job states (queued, active, uncertain, fallback, completed+slot_held) plus 2 pause→resubmit
  cases, and each showed real `admit_call` queuing iteration 2 while iteration 1 was live.
  Green: `test_design_mode.py` 49 passed (run `02951cde-466f-4fd7-9194-ef7273a77b8a`).
  Regressions all passed: admission 3 (`b42d0548-…`), ownership 2 (`d929e766-…`), router_queries 5
  (`7078cd34-…`), parent_acknowledgements 14 (`d56b8e76-…`), codex_claude_efficiency_db 10
  (`e4045fb3-…`), dispositions 3 (`a87ee582-…`). Broad suite not rerun; the 13 known
  `test_validation_export.py` failures were recorded earlier.
  Test fixture: `_admitted_review` now marks its job `completed, slot_held=false`, as
  `record_completion` does with a delivered result. The iteration-2 cancel test now asserts exact
  budgets: iteration 2 is released to `(1,0,60000,0)` and iteration 1 stays debited.
  Worker mocked, real test DB; not live integration.
- Paused-revision cancel (2026-11-02, closes the earlier open gap). `cancel` now does its claim/job
  lookup for `ACTIVE_REVIEW` and also for `paused` with `status_reason=revision_requested`, but only
  while the current iteration's hop 2 is still `claimed`/`unknown`. It reads the hop row as possibly
  None, never unpacking it. An exact admitted job (`call_id`, `pool='claude'`, `task_id`, `work_item_id`) gets
  `request_cancel_tree`, the hop becomes `cancel_requested_by_owner`, `cancellation=requested` and
  `worker_termination_confirmed=false`. An active job becomes `uncertain` with `slot_held` still true and
  `cancellation_requested` evidence; a queued job is cancelled and its budget released. If there is no
  job, the claim is `fenced_before_admission` and a late admission is refused as `design_mode_claim_inactive`.
  A paused workflow with no review, a finished review or no hop 2 (planner pause, stale-before-review,
  delivered→revise) keeps the plain cancel. Unchanged: the owner/version guard, row lock, frozen
  claims, `_reconcile` (still skips `paused`) and state classification.
  Red (run `17b673ad-419e-4aa0-a3b0-3432633a3eba`): 3 failed, 52 passed. Green: `test_design_mode.py`
  55 passed (run `ea933eda-2ba2-49c5-8ff8-c5272437a04d`). Regressions not rerun; the change is local to
  `DesignMode.cancel`. Worker mocked, real test DB; not live integration.
- Deployment state (reported 2026-11-02): schema 013 is live and the first real Claude review was
  delivered. Workflow `6fd55bb5-da9e-521a-a207-f910c0ebe70d` stays terminal `failed` on iteration 2
  (legacy shared task, not repaired or refunded). Dave reported reloading the parent MCP after the
  budget fix, and the first review replacement then completed. The live N≥2 budget path has **not**
  been exercised since, so live verification is pending; do not claim it has. No commit or live DB
  change. The legacy first browser test is still not passing (above).
- "Awaiting my decision" filter checks (2026-11-02). Changes: `service/http/query.py`,
  `service/http/app.py`, `scripts/serve-dashboard.py`, `config/dashboard-service.example.toml`,
  `docs/operations/design-mode.md`, tests. Red (run `0174b53c-3eeb-4b3c-a840-fefb4b54f808`): 12 new
  tests failed for the intended reasons (missing keyword, flag or markup). Green: `test_design_mode.py`
  67 passed (run `5044a531-99d1-49fe-a828-ea583186be65`). These cover two harness owners on one account,
  the real deliver→accept path, filtering before LIMIT, every excluded state, exact owner case,
  project intersection, 401/422, missing schema / missing binding precedence, the read-only row/audit
  invariant, config validation and old callers. `test_query_bounds` and `test_auth`: 8 passed
  (`3bc357d6-4ef7-4d36-bf60-173b3d8ccfa5`). Dashboard typecheck passed
  (`5e4f7290-077e-4f27-a89d-e93f555df1cd`). Browser `design-workflow-page.spec.ts` (route-mocked,
  presentation only; run `90efe325-54dc-4872-ab17-424a4a68b46d`): the 3 new tests passed (keyboard
  checkbox, combined filters, empty/unavailable/error/schema copy, lost binding, hidden/visible
  refresh, row removal plus a late detail reply). The pre-existing first test failed again at the
  Edge launch (same PathService/DevTools host error) before any test logic ran. The Playwright spec
  was written after the page change; only the Python page-markup test was red first.
- Filter finish (2026-11-02, after independent review `6d1720dc-e7a4-55d5-9518-780f1feb5392` ACCEPT, no
  blocking code issues). Feature implemented and review-accepted. Review medium (deployment): the live
  dashboard task used the tracked example config, and a personal owner there would break 2 example
  tests. The example stays generic.
  Help copy restored to the accepted text "Shown after Chat delivers a plan and review, until you
  accept, revise or reject." The "Not shown: …" exclusions sentence is unchanged and now asserted.
  Red first (run `1a62e4ee-42d4-4b86-8677-063b2f10295c`): 1 failed, old copy present. The source-text
  launcher assertion was replaced by behaviour tests in `tests/queries/test_design_mode_launcher.py`.
  They run the real `scripts/serve-dashboard.py` `main()` on a temp TOML with fake env values, the real
  `create_app` validation (with `dashboard_dir` forced to None) and a fake uvicorn. They check missing
  owner→None, valid owner passed exactly, and 7 invalid values raise `ValueError` before uvicorn. They
  passed first time (the behaviour was already correct), so there was no red run.
  Fresh green (run `af985ca2-9c10-4c3f-b481-c4bb83e655d9`): 77 passed (test_design_mode 67 + launcher 10).
  Browser `design-workflow-page.spec.ts` (run `9b871859-45c0-4854-ac39-8749641df88a`): the 3 new tests
  passed, and the legacy first test failed at the Edge launch (PathService 0x3F0) before any test logic.
  Run `8b7693…` (4 passed) was a focused backend test file, **not** a browser run.
  Deployment files prepared (local, never staged): `config/dashboard-service.design-mode.local.toml`
  (example copy plus `design_mode_owner_id = "bridge-monitor-parent@dave"`, owner verified on accepted
  workflow `10ea7f5e`; private, gitignored) and `config/windows-tasks.design-mode.local.json` (example
  copy, only `dashboard_config` changed; untracked, not ignored; no credentials). The switch and reload were
  later done by Dave and live-verified (see "Filter activation" below). The Postgres task is unchanged.
- Decision document return format (approved by Dave 2026-11-02; plan before code). Pure helper
  `bridge/design_delivery.py` renders, from the selected iteration's stored package only, one
  deterministic Markdown source (`design-mode-<uuid>-iteration-<N>.md`, `text/markdown`, sha256):
  verbatim brief/plan/review in collision-safe fences, package/artifact/evidence digests, source_head,
  "inspected source, not current revalidation" limits, and an empty, clearly separate parent Chat
  assessment slot. `DesignMode.retrieve` adds `decision_document` + `delivery_instructions` after the
  existing owner/project checks; no package → no body. Ready/delivered `next_action` and the
  retrieve/confirm docstrings tell Chat to publish one completed document with its own assessment,
  verify a working link, show the inline summary + link, then confirm; historical iterations are
  read-only. No schema, parameter, receipt gate or side-effect change. Tests first (red, then green).
  Implemented: `bridge/design_delivery.py` (new) and `bridge/mcp_server.py` (import, `retrieve`
  return, ready/delivered `next_action`, retrieve/confirm docstrings). Tests were appended to
  `tests/integration/test_design_mode.py`: 13 pure renderer cases plus 3 integration cases (same-package
  repeated retrieval, historical identity, no package, owner/project refusal, no side effects). The task
  grant denied a separate `tests/adapters/` file. Red (run `0761b9ce-ca60-4ccf-8521-10171b55cbc6`):
  16 failed (missing module / `decision_document`), 67 passed. One test-helper slice bug was fixed before
  green (assertions unchanged). Green: `test_design_mode.py` 83 passed (`c82b6abd-97a0-43fe-8440-06f159469097`).
  Regressions: test_startup 6 (`4a6d5bd0-…`), router_queries 5 (`17817047-…`), ownership 2
  (`c7a619c8-…`), launcher 10 (`cd556025-…`). Worker mocked; not live integration. Chat-side
  publishing and link rendering cannot be verified by Bridge. The parent MCP needs a reload before
  Dave reruns the MRI3 prompt.
- Filter activation (correction, 2026-11-02): no longer pending. Dave ran the task switch and restart.
  The parent verified `dashboard-process.json` config = `dashboard-service.design-mode.local.toml`
  (server_pid 46712). The live UI checkbox was enabled, and checking it excluded accepted `10ea7f5e` and
  failed `6fd55bb5` and showed "No workflows awaiting your decision". This is live filter verification
  only, NOT the live N≥2 budget path (still pending). Local configs are still never staged; the example
  stays generic.
