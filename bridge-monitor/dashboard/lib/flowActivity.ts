export const HANDOFF_ACTIVITY_MS = 20000;

export function formatFlowModel(model?: string | null, effort?: string | null, source?: 'Configured' | 'Requested' | 'Last handoff'): string {
  if (!model && !effort) return 'Model and effort unavailable';
  const level = effort?.startsWith('Thinking ') ? effort : effort ? `${effort} effort` : 'Effort unavailable';
  return [source, model || 'Model unavailable', level].filter(Boolean).join(' · ');
}

export function handoffActivityRemaining(time: string | undefined, now: number, available: boolean): number {
  if (!available || !time) return 0;
  const age = now - Date.parse(time);
  return age >= 0 && age < HANDOFF_ACTIVITY_MS ? HANDOFF_ACTIVITY_MS - age : 0;
}
