import type { MetricSet, Origin, Snapshot, WorkDetail, WorkItem } from './contracts.generated';

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
  session: () => request<{authenticated: boolean}>('/api/v1/session'),
  login: (operatorToken: string) => request<{authenticated: boolean}>('/api/v1/session', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operator_token: operatorToken }),
  }),
  logout: () => fetch('/api/v1/session/logout', { method: 'POST', credentials: 'same-origin' }),
  snapshot: (origin: Origin) => request<Snapshot>(`/api/v1/snapshot?origin=${origin}`),
  metrics: (filters: CohortFilters) => request<MetricSet>(`/api/v1/metrics?${queryString(filters)}`),
  workItems: (filters: CohortFilters, offset = 0) => request<WorkItem[]>(`/api/v1/work-items?${queryString(filters)}&limit=100&offset=${offset}`),
  detail: (id: string) => request<WorkDetail>(`/api/v1/work-items/${encodeURIComponent(id)}`),
  content: async (id: string) => {
    const response = await fetch(`/api/v1/content/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
    if (response.status === 401) throw new AuthenticationRequired('Operator session required');
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.text();
  },
};
