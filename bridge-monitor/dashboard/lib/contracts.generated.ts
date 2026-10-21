// Mirrors the versioned Python query DTOs; regenerate when those contracts change.
export type Origin = 'observed' | 'imported' | 'synthetic' | 'all';
export type DashboardStatus = {
  active: number; queued: number; held_uncertain: number; blocked: number;
  capture_state: 'healthy' | 'degraded' | 'unknown'; known_gaps: number;
  spool_pressure: number | null;
};
export type WorkItem = {
  work_item_id: string; task_id: string; category: string | null; origin: Origin;
  created_at: string; resolution: string | null; resolved_at: string | null;
  routed: boolean; paid_route: boolean; requested_model?: string | null;
  reported_model?: string | null; disposition_judgment?: string | null;
  guidance_group?: string; failure_categories?: string[]; repair_efforts?: string[];
  example_reuse_statuses?: string[];
};
export type Snapshot = {
  cursor: { position: number }; as_of: string; status: DashboardStatus; work_items: WorkItem[];
  worker_executions: WorkerExecution[]; capacity: Record<'claude' | 'qwen', PoolCapacity> | null;
};
export type PoolCapacity = { active: number; queued: number; held_uncertain: number };
export type WorkerExecution = { call_id: string; work_item_id: string | null;
  worker_instance_id: string | null; requested_model: string | null;
  reported_model: string | null; occurred_at: string; waiting_on_local?: boolean };
export type MetricValue = { numerator: number; denominator: number; percent: number | null };
export type MetricSet = {
  definition_version: string; cohort: Record<string, unknown>; as_of: string;
  unit: string; metrics: Record<string, MetricValue>; coverage: Record<string, MetricValue>;
  guidance_groups?: Record<string, number>;
};
export type Publication = {
  position: number; event_id: string; event_type: string; occurred_at: string;
  work_item_id: string | null; task_id: string | null; call_id: string | null;
  worker_instance_id: string | null; requested_model: string | null;
  reported_model: string | null; payload: Record<string, unknown>;
};
export type ContentReference = {
  content_id: string | null; label: string; completeness?: string;
};
export type OutputPreview = {
  call_id: string; text: string; chunks_shown: number; missing_chunks: number;
  truncated: boolean; last_event_at: string | null;
};
export type WorkDetail = {
  work_item: WorkItem;
  calls: Array<{ call_id: string; parent_call_id: string | null; requested_model: string | null; created_at: string; execution_mode: 'production' | 'shadow'; local_dispatch: boolean }>;
  messages: Array<{ message_id: string; call_id: string; role: string; direction: string; original_content_id: string | null; effective_content_id: string | null }>;
  results: Array<{ result_id: string; call_id: string; attempt_id: string | null; raw_content_id: string | null; delivered_content_id: string | null; delivered_at: string | null }>;
  models: Array<{ model_execution_id: string; attempt_id: string; call_id: string; requested_model: string | null; reported_model: string | null; model_evidence: Record<string, unknown>; status: string }>;
  usage: Array<{ observation_id: string; model_execution_id: string; scope: string;
    is_cumulative: boolean; input_tokens: number | null; output_tokens: number | null;
    cache_read_input_tokens: number | null; cache_creation_input_tokens: number | null;
    provenance: string; price_basis: string | null }>;
  decisions: Array<{ decision_id: string; route: string; eligibility: string; reason: string | null; policy_version: string | null; child_call_id: string | null; guidance_manifest: Record<string, unknown>; gate_facts: Record<string, unknown>; decided_at: string }>;
  supplied_decision_ids?: string[];
  dispositions: Array<{ disposition_id: string; result_id: string; actor_id: string; stage: string; judgment: string; reason: string | null; supersedes_id: string | null; recorded_at: string; details?: Record<string, unknown>; part_id?: string | null; input_sha256?: string | null; output_result_id?: string | null; valid_parent_report?: boolean | null }>;
  resolutions: Array<{ resolution_id: string; outcome: string; final_result_id: string | null; recorded_at: string }>;
  events: Array<{ event_id: string; event_type: string; occurred_at: string; received_at: string; call_id: string | null; worker_instance_id: string | null; capture_completeness: string; payload: Record<string, unknown> }>;
  events_truncated?: boolean;
  events_gap_after?: number | null;
  agent_statuses?: Array<{ call_id: string; agent_state: string | null;
    agent_reachable: boolean | null; last_model_output_at: string | null;
    timeout_reason: string | null }>;
  recovery_decisions?: Array<{ call_id: string; recorded_at: string; outcomes: Array<{ child_call_id: string; recovery_call_id: string; state: string; reason: string | null }> }>;
  content_states: Record<string, { content_id: string; completeness: string; byte_length: number; content_type: string; encoding: string }>;
  guidance_packs?: Array<{ pack_version_id: string; pack_id: string; version_label: string; sha256: string; items: Array<Record<string, unknown>> }>;
  example_reviews?: Array<{ review_id: string; pack_version_id: string; example_id: string; content_id: string; source_work_item_id: string | null; source_result_id: string | null; source_origin?: Origin | null; reviewer_id: string; status: string; permitted_use: string; exposure: string; scope: unknown; evidence: Record<string, unknown>; supersedes_id: string | null; reviewed_at: string }>;
  evaluations?: Array<{ evaluation_id: string; result_id: string; evidence: Record<string, unknown>; recorded_at: string }>;
  result_links?: Array<{ from_result_id: string; to_result_id: string; relation: string }>;
};
export type ShadowCohort = {
  definition_version: string; attempted_tasks: number; queued_shadow: number;
  result_captured: number; independent_evaluated: number;
  states: Record<string, number>; outcomes: Record<string, number>;
  reasons: Record<string, number>; model_profiles: Record<string, number>;
  prompt_versions: Record<string, number>; verifier_refs: Record<string, number>;
  categories: Record<string, number>; output_contracts: Record<string, number>;
  context_bytes: { samples: number; median: number | null };
  queue_latency_ms: { samples: number; median: number | null };
  shadow_execution_latency_ms: { samples: number; median: number | null };
  verifier_latency_ms: { samples: number; median: number | null };
  usage_coverage: { qwen: number; paid: number };
  unknown_usage: { qwen: number; paid: number };
  provider_usage: Array<{ call_id: string; input_tokens: number | null;
    output_tokens: number | null; provenance: string; price_basis: string | null }>;
  conditional_avoided_paid_calls: number | null; conditional_savings_reason: string;
  billed_savings: number | null; billed_savings_reason: string;
};
export type RouterAudit = {
  work_item: WorkItem; paid_call_id: string | null; shadow_call_id: string | null;
  parent_decision: { reason: string | null; route: string; gate_facts: Record<string, unknown> } | null;
  shadow_decision: { reason: string | null; gate_facts: Record<string, unknown> } | null;
  decisions: Array<{ decision_id: string; execution_mode: string; route: string;
    reason: string | null; policy_version: string; gate_facts: Record<string, unknown>; decided_at: string }>;
  jobs: Array<{ call_id: string; pool: string; execution_mode: string; state: string;
    requested_model: string | null; admitted_at: string; launch_claimed_at: string | null;
    slot_held: boolean }>;
  paid_result: { result_id: string; delivered_at: string | null } | null;
  shadow_result: { result_id: string; delivered_at: string | null } | null;
  shadow_parts: Array<{ part_id: string; content_id: string }>;
  evaluation: ({ state: string; reason: string; verifier_image: string;
    elapsed_seconds: number | null; recorded_at: string }) | null;
  evaluation_history: Array<{ evaluation_id: string; evidence: Record<string, unknown>;
    recorded_at: string }>;
  prompt_refs: { original: string | null; paid_effective: string | null;
    shadow_effective: string | null };
  result_refs: { paid: string | null; shadow_raw: string | null; shadow_parsed: string | null };
  dispositions: Array<{ disposition_id: string; stage: string; judgment: string;
    reason: string | null; recorded_at: string; supersedes_id: string | null }>;
  usage: Array<{ call_id: string; scope: string; input_tokens: number | null;
    output_tokens: number | null; provenance: string; price_basis: string | null }>;
  timeline: Array<{ event_type: string; call_id: string | null; occurred_at: string;
    payload: Record<string, unknown> }>;
};
