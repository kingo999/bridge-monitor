import type { CohortFilters } from '@/lib/api';

export default function CohortControls({ filters, onChange, mode, onMode, paused, onPause }: {
  filters: CohortFilters; onChange: (filters: CohortFilters) => void;
  mode: 'live' | 'history'; onMode: (mode: 'live' | 'history') => void;
  paused: boolean; onPause: () => void;
}) {
  return <div className="toolbar">
    <div className="segmented" role="group" aria-label="View mode">
      <button type="button" className={mode === 'live' ? 'active' : ''} onClick={() => onMode('live')}>Live</button>
      <button type="button" className={mode === 'history' ? 'active' : ''} onClick={() => onMode('history')}>History</button>
    </div>
    <label>Origin <select value={filters.origin} onChange={e => onChange({ ...filters, origin: e.target.value as CohortFilters['origin'] })}>
      <option value="observed">Observed</option><option value="synthetic">Sample data</option>
      <option value="imported">Imported</option><option value="all">All origins</option>
    </select></label>
    <label>Consumer <select value={filters.consumerStage} onChange={e => onChange({ ...filters, consumerStage: e.target.value })}>
      <option value="claude">Claude</option><option value="gpt">GPT / Codex</option>
    </select></label>
    {mode === 'history' && <>
      <label>From <input type="datetime-local" value={filters.createdFrom} onChange={e => onChange({ ...filters, createdFrom: e.target.value })} /></label>
      <label>Until <input type="datetime-local" value={filters.createdUntil} onChange={e => onChange({ ...filters, createdUntil: e.target.value })} /></label>
      <label className="check"><input type="checkbox" checked={filters.completedOnly} onChange={e => onChange({ ...filters, completedOnly: e.target.checked })} /> Completed</label>
    </>}
    <label>Guidance <input aria-label="Guidance group" list="guidance-groups" value={filters.guidanceGroup}
      onChange={e => onChange({ ...filters, guidanceGroup: e.target.value })} placeholder="Pack ID / state" /></label>
    <datalist id="guidance-groups"><option value="no_guidance" /><option value="mixed" /><option value="unknown" /><option value="not_dispatched" /></datalist>
    <label>Failure <select aria-label="Failure category" value={filters.failureCategory} onChange={e => onChange({ ...filters, failureCategory: e.target.value })}>
      <option value="">All</option><option value="incorrect_behavior">Incorrect behavior</option><option value="repository_convention_violated">Repository convention</option><option value="invented_api">Invented API</option><option value="scope_violation">Scope violation</option><option value="invalid_output_format">Invalid format</option><option value="insufficient_context">Insufficient context</option><option value="infrastructure_failure">Infrastructure</option>
    </select></label>
    <label>Repair <select aria-label="Repair effort" value={filters.repairEffort} onChange={e => onChange({ ...filters, repairEffort: e.target.value })}>
      <option value="">All</option><option value="none">None</option><option value="minor">Minor</option><option value="substantial">Substantial</option><option value="complete_replacement">Replacement</option>
    </select></label>
    <label>Reuse <select aria-label="Example reuse" value={filters.exampleReuseStatus} onChange={e => onChange({ ...filters, exampleReuseStatus: e.target.value })}>
      <option value="">All</option><option value="approved">Approved</option><option value="candidate">Candidate</option><option value="rejected">Rejected</option><option value="unknown">Unknown</option>
    </select></label>
    <button className="pause-button" type="button" onClick={onPause} aria-pressed={paused}>{paused ? 'Resume display' : 'Pause display'}</button>
  </div>;
}
