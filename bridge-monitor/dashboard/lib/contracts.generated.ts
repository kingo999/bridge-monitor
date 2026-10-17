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
  reported_model: string | null; occurred_at: string };
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
export type WorkDetail = {
  work_item: WorkItem;
  calls: Array<{ call_id: string; parent_call_id: string | null; requested_model: string | null; created_at: string; local_dispatch: boolean }>;
  messages: Array<{ message_id: string; call_id: string; role: string; direction: string; original_content_id: string | null; effective_content_id: string | null }>;
  results: Array<{ result_id: string; call_id: string; attempt_id: string | null; raw_content_id: string | null; delivered_content_id: string | null; delivered_at: string | null }>;
  models: Array<{ model_execution_id: string; attempt_id: string; call_id: string; requested_model: string | null; reported_model: string | null; model_evidence: Record<string, unknown>; status: string }>;
  decisions: Array<{ decision_id: string; route: string; eligibility: string; reason: string | null; policy_version: string | null; child_call_id: string | null; guidance_manifest: Record<string, unknown>; gate_facts: Record<string, unknown>; decided_at: string }>;
  supplied_decision_ids?: string[];
  dispositions: Array<{ disposition_id: string; result_id: string; actor_id: string; stage: string; judgment: string; reason: string | null; supersedes_id: string | null; recorded_at: string; details?: Record<string, unknown>; part_id?: string | null; input_sha256?: string | null; output_result_id?: string | null }>;
  resolutions: Array<{ resolution_id: string; outcome: string; final_result_id: string | null; recorded_at: string }>;
  events: Array<{ event_id: string; event_type: string; occurred_at: string; received_at: string; call_id: string | null; worker_instance_id: string | null; capture_completeness: string; payload: Record<string, unknown> }>;
  events_truncated?: boolean;
  recovery_decisions?: Array<{ call_id: string; recorded_at: string; outcomes: Array<{ child_call_id: string; recovery_call_id: string; state: string; reason: string | null }> }>;
  content_states: Record<string, { content_id: string; completeness: string; byte_length: number; content_type: string; encoding: string }>;
  guidance_packs?: Array<{ pack_version_id: string; pack_id: string; version_label: string; sha256: string; items: Array<Record<string, unknown>> }>;
  example_reviews?: Array<{ review_id: string; pack_version_id: string; example_id: string; content_id: string; source_work_item_id: string | null; source_result_id: string | null; source_origin?: Origin | null; reviewer_id: string; status: string; permitted_use: string; exposure: string; scope: unknown; evidence: Record<string, unknown>; supersedes_id: string | null; reviewed_at: string }>;
  evaluations?: Array<{ evaluation_id: string; result_id: string; evidence: Record<string, unknown>; recorded_at: string }>;
  result_links?: Array<{ from_result_id: string; to_result_id: string; relation: string }>;
};
