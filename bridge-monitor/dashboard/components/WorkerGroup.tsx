import type { WorkerExecution } from '@/lib/contracts.generated';
import { formatFlowModel } from '@/lib/flowActivity';
import NodeActivityStatus, { nodeActivityClass, nodeActivityState } from './NodeActivityStatus';

function executionModel(item: WorkerExecution) {
  return formatFlowModel(item.reported_model || item.requested_model, item.configured_effort,
    item.reported_model ? undefined : 'Requested');
}

export default function WorkerGroup({ active, queued, held, blocked, executions, expanded, onToggle, onSelect, paused = false, available = true }: {
  active: number; queued: number; held: number; blocked: number; executions: WorkerExecution[];
  expanded: boolean; onToggle: () => void; onSelect: (id: string) => void;
  paused?: boolean; available?: boolean;
}) {
  const waitingLocal = Math.min(active, executions.filter(item => item.waiting_on_local === true).length);
  const state = nodeActivityState({ active: active - waitingLocal, queued: queued + waitingLocal,
    uncertain: held, available, paused });
  const models = executions.filter(item => item.active === true).map(executionModel);
  const labels = models.length ? Array.from(new Set(models)) : [formatFlowModel()];
  return <div className={`worker-group${nodeActivityClass(state)}`}>
    <button className="worker-heading" type="button" onClick={onToggle} aria-expanded={expanded}>
      <span className="worker-glyph">✳</span><span><strong>Claude workers</strong><small>{active} active · {queued} queued</small></span>
      <span aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
    </button>
    <NodeActivityStatus state={state} queued={queued} waiting={waitingLocal}
      label={state === 'waiting' && waitingLocal > 0 ? 'Waiting for Skynet' : undefined} />
    {active > 0 && <div className="worker-models">
      {labels.map(label => <small className="flow-model-details" key={label}>{label}</small>)}
    </div>}
    <div className="capacity-note">{held} held uncertain · {blocked} blocked starts</div>
    {expanded && <div className="worker-executions">
      {executions.length ? executions.slice(0, 4).map(item => <button key={item.call_id} type="button"
        onClick={() => item.work_item_id && onSelect(item.work_item_id)} className="worker-execution">
        <span className={`dot ${item.waiting_on_local ? 'waiting-dot' : 'blue'}`} /><span><strong>{item.worker_instance_id || item.call_id}</strong>
          <small>{executionModel(item)} · {item.waiting_on_local ? 'waiting for Skynet' : item.active === false ? 'held uncertain' : 'active'}</small></span>
      </button>) : <p className="muted">No active execution details observed.</p>}
    </div>}
  </div>;
}
