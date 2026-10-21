import type { WorkerExecution } from '@/lib/contracts.generated';
import NodeActivityStatus, { nodeActivityClass, nodeActivityState } from './NodeActivityStatus';

export default function WorkerGroup({ active, queued, held, blocked, executions, expanded, onToggle, onSelect, paused = false, available = true }: {
  active: number; queued: number; held: number; blocked: number; executions: WorkerExecution[];
  expanded: boolean; onToggle: () => void; onSelect: (id: string) => void;
  paused?: boolean; available?: boolean;
}) {
  const waitingLocal = Math.min(active, executions.filter(item => item.waiting_on_local === true).length);
  const state = nodeActivityState({ active: active - waitingLocal, queued: queued + waitingLocal,
    uncertain: held, available, paused });
  return <div className={`worker-group${nodeActivityClass(state)}`}>
    <button className="worker-heading" type="button" onClick={onToggle} aria-expanded={expanded}>
      <span className="worker-glyph">✳</span><span><strong>Claude workers</strong><small>{active} active · {queued} queued</small></span>
      <span aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
    </button>
    <NodeActivityStatus state={state} queued={queued} waiting={waitingLocal}
      label={state === 'waiting' && waitingLocal > 0 ? 'Waiting for Skynet' : undefined} />
    <div className="capacity-note">{held} held uncertain · {blocked} blocked starts</div>
    {expanded && <div className="worker-executions">
      {executions.length ? executions.slice(0, 4).map(item => <button key={item.call_id} type="button"
        onClick={() => item.work_item_id && onSelect(item.work_item_id)} className="worker-execution">
        <span className={`dot ${item.waiting_on_local ? 'waiting-dot' : 'blue'}`} /><span><strong>{item.worker_instance_id || item.call_id}</strong>
          <small>{item.reported_model || item.requested_model || 'Model unknown'} · {item.waiting_on_local ? 'waiting for Skynet' : 'active'}</small></span>
      </button>) : <p className="muted">No active execution details observed.</p>}
    </div>}
  </div>;
}
