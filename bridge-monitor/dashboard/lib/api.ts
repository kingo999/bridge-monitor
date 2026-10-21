import type { MetricSet, Origin, OutputPreview, RouterAudit, ShadowCohort, Snapshot, WorkDetail, WorkItem } from './contracts.generated';

export class AuthenticationRequired extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...init });
  if (response.status === 401) throw new AuthenticationRequired('Operator session required');
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export type CohortFilters = {
  origin: Origin; consumerStage: string; createdFrom: string; createdUntil: string;
  completedOnly: boolean; guidanceGroup: string; failureCategory: string;
  repairEffort: string; exampleReuseStatus: string;
};

export type OperatorSession = { authenticated: boolean; work_item_max_limit?: number };
export type RouterFilters = { modelProfile: string; promptVersion: string; verifierRef: string };
export type ClassifierFilters = { originKind: string; model: string; profileId: string; captureMethod?: string };
export type Dist = { samples: number; median: number | null; p95: number | null; max: number | null };
export type ClassifierActivity = {
  available: boolean; checked_at: string; queued: number | null; classifying: number | null;
  uncertain: number | null;
  latest: { observation_id: string; work_item_id: string | null; state: string;
    requested_model: string; captured_at: string; dispatched_at: string | null;
    completed_at: string | null; rec_route: string | null; origin_kind: string;
    capture_method: string } | null;
};
export type ClassifierCohort = {
  definition_version: string; available: boolean; reason?: string; scope_note?: string;
  counts: Record<'submitted' | 'attempted' | 'completed' | 'failed' | 'skipped' | 'uncertain' | 'cancelled' | 'pending' | 'unknown_outcome', number>;
  skip_reasons: Record<string, number>; error_types: Record<string, number>;
  contract_errors: Record<string, number>;
  recommendations: { routes: Record<string, number>; categories: Record<string, number> };
  policy: { allowed: number; blocked: number; block_reasons: Record<string, number>; unknown_code_capacity_blocks?: number; other_blocked?: number };
  capture?: { observations: Record<string, number>; unique_work_items: number; unique_work_items_by_method: Record<string, number>; overlap_work_items: number; note: string };
  fact_coverage?: Record<string, Record<'true' | 'false' | 'unknown', number>>;
  reference: { labelled: number; unlabelled: number; raw_route_acceptable: number; raw_route_preferred: number; allowed_and_acceptable: number };
  actual_route: { known: number; unknown: number; differs_from_recommendation: number; note: string };
  follow_up: { with_verification_or_correction: number; with_operator_judgment: number; completed: number };
  latency_ms: Record<'queue' | 'request' | 'first_content' | 'inference_reported' | 'load_reported' | 'end_to_end', Dist>;
  tokens: { input_total: number; output_total: number; provenance: Record<string, number>; attempted: number };
  router_evidence?: { note: string; completed_with_work_item: number; qwen_shadow_independent_evaluation: Record<string, number>; paid_result_dispositions: Record<string, number> };
  backend_residency: Record<string, number>;
  cost: { local_provider_charge_usd: number | null; electricity_usd: number | null; electricity_reason: string; hypothetical_avoided_paid_calls: number | null; billed_savings: number | null };
  filters: { models: string[]; profiles: string[]; rubrics: string[]; origins: string[] };
};

export function queryString(filters: CohortFilters): string {
  const params = new URLSearchParams({ origin: filters.origin, consumer_stage: filters.consumerStage });
  if (filters.createdFrom) params.set('created_from', new Date(filters.createdFrom).toISOString());
  if (filters.createdUntil) params.set('created_until', new Date(filters.createdUntil).toISOString());
  if (filters.completedOnly) params.set('completed_only', 'true');
  if (filters.guidanceGroup) params.set('guidance_group', filters.guidanceGroup);
  if (filters.failureCategory) params.set('failure_category', filters.failureCategory);
  if (filters.repairEffort) params.set('repair_effort', filters.repairEffort);
  if (filters.exampleReuseStatus) params.set('example_reuse_status', filters.exampleReuseStatus);
  return params.toString();
}

export const api = {
  session: () => request<OperatorSession>('/api/v1/session'),
  login: (operatorToken: string) => request<OperatorSession>('/api/v1/session', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operator_token: operatorToken }),
  }),
  logout: () => fetch('/api/v1/session/logout', { method: 'POST', credentials: 'same-origin' }),
  snapshot: (origin: Origin) => request<Snapshot>(`/api/v1/snapshot?origin=${origin}`),
  metrics: (filters: CohortFilters) => request<MetricSet>(`/api/v1/metrics?${queryString(filters)}`),
  routerCohort: (filters: CohortFilters, router: RouterFilters) => {
    const params = new URLSearchParams(queryString(filters));
    if (router.modelProfile) params.set('model_profile', router.modelProfile);
    if (router.promptVersion) params.set('prompt_version', router.promptVersion);
    if (router.verifierRef) params.set('verifier_ref', router.verifierRef);
    return request<ShadowCohort>(`/api/v1/router/cohort?${params}`);
  },
  classifierActivity: (signal?: AbortSignal) => request<ClassifierActivity>('/api/v1/classifier/activity', { signal }),
  classifierCohort: (filters: ClassifierFilters, signal?: AbortSignal) => {
    const params = new URLSearchParams({ origin_kind: filters.originKind || 'all' });
    if (filters.captureMethod) params.set('capture_method', filters.captureMethod);
    if (filters.model) params.set('model', filters.model);
    if (filters.profileId) params.set('profile_id', filters.profileId);
    return request<ClassifierCohort>(`/api/v1/classifier/cohort?${params}`, { signal });
  },
  routerAudit: (id: string) => request<RouterAudit>(`/api/v1/router/audit/${encodeURIComponent(id)}`),
  workItems: (filters: CohortFilters, offset = 0, limit = 100) => request<WorkItem[]>(`/api/v1/work-items?${queryString(filters)}&limit=${limit}&offset=${offset}`),
  detail: (id: string) => request<WorkDetail>(`/api/v1/work-items/${encodeURIComponent(id)}`),
  outputPreview: (workId: string, callId: string) =>
    request<OutputPreview>(`/api/v1/work-items/${encodeURIComponent(workId)}/calls/${encodeURIComponent(callId)}/output-preview`),
  content: async (id: string) => {
    const response = await fetch(`/api/v1/content/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
    if (response.status === 401) throw new AuthenticationRequired('Operator session required');
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.text();
  },
};
