export const HANDOFF_ACTIVITY_MS = 20000;

export function handoffActivityRemaining(time: string | undefined, now: number, available: boolean): number {
  if (!available || !time) return 0;
  const age = now - Date.parse(time);
  return age >= 0 && age < HANDOFF_ACTIVITY_MS ? HANDOFF_ACTIVITY_MS - age : 0;
}
