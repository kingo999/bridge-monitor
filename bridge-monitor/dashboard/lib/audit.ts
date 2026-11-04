export type AuditView = 'executions' | 'network';
export type AuditLaunch = { key: number; view: AuditView | 'handoffs'; token?: string; usage?: string; run?: string; q?: string };
export type AuditCoverage = {
  indexed: number; invalid: number; source_high: number | null; indexed_high: number;
  execution_pending: number; synced_at: string | null; health_processed_at: string | null;
  complete: boolean; scope: string;
  execution_version?: number;
};
export type AuditRow = {
  id: string; time?: string; project?: string; handler?: string; status?: string;
  usage?: boolean; has_usage?: boolean; destination?: string; bytes?: number | null;
  kind?: string; layer?: string; process?: string; run?: string; attribution?: string;
  error?: string; data: Record<string, unknown>;
};
export type AuditPage = {
  token: string; total: number; rows: AuditRow[]; next: number | null;
  coverage: AuditCoverage; filters: Record<string, unknown>;
};
export async function auditRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api/v1/audit/' + path, {
    credentials: 'same-origin', cache: 'no-store', signal,
    ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Audit data unavailable');
  }
  return response.json();
}
