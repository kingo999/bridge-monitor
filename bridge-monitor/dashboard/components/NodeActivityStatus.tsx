export type NodeActivityState = 'working' | 'waiting' | 'idle' | 'uncertain' | 'unavailable' | 'paused';

export function nodeActivityState({ active, queued, uncertain = 0, available = true, paused = false }: {
  active: number; queued: number; uncertain?: number; available?: boolean; paused?: boolean;
}): NodeActivityState {
  if (paused) return 'paused';
  if (!available) return 'unavailable';
  if (active > 0) return 'working';
  if (queued > 0) return 'waiting';
  if (uncertain > 0) return 'uncertain';
  return 'idle';
}

export function nodeActivityClass(state: NodeActivityState): string {
  return ['working', 'waiting', 'uncertain'].includes(state) ? ` is-${state}` : '';
}

export default function NodeActivityStatus({ state, label, queued = 0, waiting = 0 }: {
  state: NodeActivityState; label?: string; queued?: number; waiting?: number;
}) {
  const labels: Record<NodeActivityState, string> = {
    working: 'Working', waiting: 'Waiting · queued', idle: 'Idle',
    uncertain: 'Execution uncertain', unavailable: 'Activity unavailable', paused: 'Display paused',
  };
  return <small className={`node-status state-${state}`} data-state={state}>
    <span className="node-state-dot" aria-hidden="true" />{label || labels[state]}
    {state === 'working' && queued > 0 && <span className="node-queue">{queued} queued</span>}
    {state === 'working' && waiting > 0 && <span className="node-queue">{waiting} awaiting Skynet</span>}
  </small>;
}
