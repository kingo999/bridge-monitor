import type { WorkerExecution } from '@/lib/contracts.generated';

export default function WorkerGroup({ active, queued, held, blocked, executions, expanded, onToggle, onSelect }: {
  active: number; queued: number; held: number; blocked: number; executions: WorkerExecution[];
  expanded: boolean; onToggle: () => void; onSelect: (id: string) => void;
}) {
  return <div className="worker-group">
    <button className="worker-heading" type="button" onClick={onToggle} aria-expanded={expanded}>
      <span className="worker-glyph">✳</span><span><strong>Claude workers</strong><small>{active} active · {queued} queued</small></span>
      <span aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
    </button>
    <div className="capacity-note">{held} held uncertain · {blocked} blocked starts</div>
    {expanded && <div className="worker-executions">
      {executions.length ? executions.slice(0, 4).map(item => <button key={item.call_id} type="button"
        onClick={() => item.work_item_id && onSelect(item.work_item_id)} className="worker-execution">
        <span className="dot blue" /><span><strong>{item.worker_instance_id || item.call_id}</strong>
          <small>{item.reported_model || item.requested_model || 'Model unknown'} · active</small></span>
      </button>) : <p className="muted">No active execution details observed.</p>}
    </div>}
  </div>;
}
