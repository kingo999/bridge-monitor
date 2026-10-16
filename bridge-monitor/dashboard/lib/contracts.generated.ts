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
  decisions: Array<{ decision_id: string; route: string; eligibility: string; reason: string | null; policy_version: string | null; guidance_manifest: Record<string, unknown>; gate_facts: Record<string, unknown>; decided_at: string }>;
  dispositions: Array<{ disposition_id: string; result_id: string; actor_id: string; stage: string; judgment: string; reason: string | null; supersedes_id: string | null; recorded_at: string }>;
  resolutions: Array<{ resolution_id: string; outcome: string; final_result_id: string | null; recorded_at: string }>;
  events: Array<{ event_id: string; event_type: string; occurred_at: string; received_at: string; call_id: string | null; worker_instance_id: string | null; capture_completeness: string; payload: Record<string, unknown> }>;
  content_states: Record<string, { content_id: string; completeness: string; byte_length: number; content_type: string; encoding: string }>;
};
