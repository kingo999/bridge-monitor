# Codex/Claude task presets and efficiency summary

Status: backend presets, report fields, efficiency summary, CSV/JSONL export and the dashboard
efficiency comparison table are implemented. This is a feature description, not an overall
product or enterprise approval, and it reports no measured savings.
No SQL migration, no new endpoint, no policy/runtime/pin/deadline/budget change.

## Task presets (`contracts/codex_claude.py`, `bridge/router/codex_claude.py`)

- `TASK_PRESET_VERSION = "task-presets-v1"`.
- `TASK_PRESETS`: `mechanical -> haiku` (requires caller-reported complexity=low and risk=low),
  `standard -> sonnet`, `deep_review -> opus`. Presets pick an existing profile slot only;
  model id and effort come from the policy snapshot pinned in the decision.
- Input: optional `task.context["task_preset"]`, passed as
  `evaluate_codex_claude(policy, facts, host, override="auto", task_preset=None)`.
- Gate order is unchanged: workflow_disabled, scope_not_authorized, context_unavailable,
  durable_logging_unavailable, invalid_override. Then `invalid_task_preset` (any supplied
  value that is not a known name, including `""`). Then owner_tools_required,
  caller_keep_owner (`codex`), family override, preset or complexity rule, and profile
  eligibility (`profile_disabled` / `execution_profile_unavailable`).
- Preset rules (`override="auto"` only): `mechanical` needs low/low, otherwise the decision
  is `blocked/task_preset_incompatible`. A preset weaker than the legacy complexity rule
  (for example `standard` on complex work, where the legacy rule picks Opus) is also
  `task_preset_incompatible`; there is no quiet fallback. On success the reason is `task_preset`.
- An explicit family override (`haiku|sonnet|opus`) or `codex` beats a known preset. The
  preset is then recorded as `status="overridden"` with `applied=null`.
- Caller facts (complexity/risk/category) are never mutated.
- `CodexClaudeDecision.task_preset` (optional dict) is present only when a preset was supplied:
  `{requested, applied|null, version, status: applied|overridden|blocked|not_applied, reason, profile|null}`.
  When absent, serialization omits the key, so legacy payloads, digests and old stored JSON
  are byte-identical and still validate.

## Report (`service/queries/codex_claude.py::query_codex_claude`)

All old top-level and row keys keep their old meaning. Additive row fields:

| field | meaning |
|---|---|
| `task_preset` | stored decision preset record or null |
| `applied_preset` | preset id only when `status=="applied"` |
| `preset_evidence` | `applied`, `overridden`, `blocked`, `not_applied`, `legacy_no_preset` (decision without preset), or `missing_decision` (no decision evidence) |
| `configured_model` / `configured_effort` | from the stored decision `policy_snapshot.profiles[selected_profile]`, never the active policy |
| `effort_source` | `decision_snapshot` or null |
| `reported_models` | sorted distinct reported models from ALL production executions (root and child calls) or direct runtime |
| `efficiency_model` | single reported model, `mixed` (more than one), or `unknown` (none, or some execution has no reported model while others do); requested/selected/configured is never used as reported |
| `efficiency_effort` | configured effort; `unattributed` if the reported model differs from the pinned one, models are mixed, or an execution is uncovered; `unknown` if there is no snapshot effort |
| `parent_outcome` | `accepted`, `revised`, `replaced`, `unused`, `pending` (one delivery, no effective disposition), `ambiguous` (more than one delivery or more than one effective disposition), or `unknown` (no delivered-result evidence) |
| `coverage` | `{duration: bool, cost: receipt_backed\|unknown, counters: {counter: bool}}` |

Attribution coverage unit: an execution is identified by `events.model_execution_id` on
`model.selected`, `model.reported` or `usage.reported` events (the same convention as usage
reconciliation in `codex_claude_usage.py`). Each execution must carry its own reported model;
one reported execution on a call does not cover a second execution on that call that has only
selected/usage events, so the workflow becomes `unknown`/`unattributed`. Repeated observations of
one execution count once. Legacy fallback: a production call with no execution-keyed events is
covered at call level by any reported model on its events; call-level lifecycle events without an
execution ID (for example `call.dispatched`) do not create an extra uncovered execution when the
call's execution reports exist. Child calls without any reported model stay uncovered (conservative
`unknown`). The direct Codex runtime path is unchanged (`direct_task.recorded` runtime model).

Receipts are NOT de-duplicated by hash in the query. Uniqueness per call (`cc_receipts`) and per-execution provider-quote dedup (excluding settled calls) are authoritative upstream, so identical hashes on separate executions each contribute their amount to the row cost and appear in `receipt_hashes`.

New top-level `efficiency` = `efficiency_summary(rows)` (a pure function), definition
`codex-claude-efficiency-v1`, unit `workflows`. It groups by
`(applied_preset or preset_evidence, efficiency_model, efficiency_effort)`. Each group has:
`workflows`, `outcomes` (all seven keys), `outcome_denominator`,
`human_accepted=null`, `checks_passed=null`, `duration_seconds{samples,median,p95}`,
`counters[input|output|cache_read_input|cache_creation_input _tokens]{known_sum|null, covered_workflows, unknown_workflows}`
(cache subsets are never added to totals), and
`api_equivalent_usd{basis, known_subtotal|null, covered_workflows, unknown_workflows, is_total_job_cost=false}`.
The summary also sets `parent_coordinator_overhead="unmeasured"`. It makes no savings,
subscription-dollar, ranking or cost-per-accepted claims.

Outcomes come from `history._gpt_work_judgment_evidence`, which uses the same delivery,
content-hash, provenance and supersession query. `_gpt_work_judgments` keeps its contract:
it returns only valid single effective judgments.

The CSV export appends `applied_preset, preset_evidence, configured_effort, parent_outcome`
after the existing columns. JSONL rows carry every new field.

## Dashboard (`dashboard/components/CodexClaudeOverview.tsx`)

The Codex/Claude overview renders an "Efficiency comparison" table with columns Preset /
evidence, Reported model, Configured effort, Workflows, Parent outcomes, Duration median / p95,
Token counters and API-equivalent subtotal. Groups are listed in no ranked order. Compatibility
and null evidence: when a report has no `efficiency` object (older backend) the UI says the
comparison is unavailable and values are unknown, not zero; null sums, subtotals, human
verification and checks render as "Unknown", and `unknown`/`mixed`/`unattributed` buckets get
explicit labels. Underlying field names and types are unchanged.

## Known limitations and caveats

- Rollback: older builds whose decision contract uses `extra="forbid"` reject persisted decisions
  that carry `task_preset`. Before rolling back, keep (or upgrade) the decision reader so stored
  preset-bearing decisions still parse. No migration is provided now.
- An unknown preset is intentionally `invalid_task_preset` even when an explicit override is also
  supplied, because the preset gate runs before override handling.
- The existing headline `cost.known_subtotal` shows `"0"` when no workflow has receipt coverage
  (`known_workflows=0`). This predates the feature, is out of scope here, and is disclosed by the
  known/unknown workflow counts; the efficiency groups use `null` instead.
