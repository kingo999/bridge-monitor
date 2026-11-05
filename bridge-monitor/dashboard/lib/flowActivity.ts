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

export const CHAT_PREPARING_MS = 300000;

export type ChatActivity = {
  state: 'preparing' | 'awaiting_decision'; workflow_id: string; iteration: number;
  since: string; expires_in_seconds?: number;
};

// Preparing lasts from the review retrieval until delivery is confirmed, bounded by a
// short expiry so an abandoned retrieval cannot keep the tile lit.
export function chatPreparingRemaining(activity: ChatActivity | undefined, now: number, available: boolean): number {
  if (!available || activity?.state !== 'preparing') return 0;
  const since = Date.parse(activity.since);
  if (Number.isNaN(since)) return 0;
  const seconds = activity.expires_in_seconds;
  const window = typeof seconds === 'number' && seconds > 0 ? Math.min(seconds * 1000, CHAT_PREPARING_MS) : CHAT_PREPARING_MS;
  return Math.min(window, Math.max(0, since + window - now));
}
