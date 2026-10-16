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
    <button className="pause-button" type="button" onClick={onPause} aria-pressed={paused}>{paused ? 'Resume display' : 'Pause display'}</button>
  </div>;
}
