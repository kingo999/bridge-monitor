import type { WorkItem } from '@/lib/contracts.generated';
import { useState } from 'react';

function route(item: WorkItem): string {
  if (item.execution_path === 'direct_codex') return 'Not applicable';
  return item.routed ? 'Routed' : item.paid_route ? 'Not routed'
    : item.resolution ? 'No route record' : 'Pending';
}

export default function WorkList({ items, selectedId, onSelect, search, onSearch, loading, more, onMore }: {
  items: WorkItem[]; selectedId: string | null; onSelect: (id: string) => void;
  search: string; onSearch: (value: string) => void; loading: boolean;
  more: boolean; onMore: () => void;
}) {
  const [path, setPath] = useState('all');
  const shown = items.filter(item => path === 'all' || (item.execution_path || 'unknown') === path);
  return <section className="panel work-panel" aria-label="Recent and historical work">
    <div className="panel-heading"><div><h2>Recent & active work <small>({items.length} shown)</small></h2>
      <p>Choose a work item to inspect its exchange</p></div>
      <label>Execution path <select aria-label="Execution path" value={path} onChange={e => setPath(e.target.value)}>
        <option value="all">All execution paths</option><option value="bridge">Bridge-delegated</option>
        <option value="direct_codex">Direct Codex</option><option value="unknown">Unknown</option>
      </select></label>
      <input aria-label="Search work items" className="work-search" type="search" placeholder="Search ID, category or bypass reason"
        value={search} onChange={e => onSearch(e.target.value)} /></div>
    <div className="work-table-wrap"><table className="work-table"><thead><tr>
      <th>Work item</th><th>Execution</th><th>Model</th><th>Guidance</th><th>Skynet routing</th><th>Status</th><th>Created</th>
    </tr></thead><tbody>{shown.map(item => <tr key={item.work_item_id} className={selectedId === item.work_item_id ? 'selected' : ''}>
      <td><button type="button" onClick={() => onSelect(item.work_item_id)} aria-current={selectedId === item.work_item_id ? 'true' : undefined}>
        <strong>{item.category || item.work_item_id}</strong><small>{item.work_item_id}</small></button></td>
      <td><span>{item.execution_path === 'direct_codex' ? 'Direct Codex' : item.execution_path === 'bridge' ? 'Bridge-delegated' : 'Unknown'}</span>
        {item.bypass_reason && <small title={item.bypass_reason_text}>{item.bypass_reason}</small>}</td>
      <td>{item.reported_model || item.requested_model || <span className="muted">Unknown</span>}</td>
      <td>{item.guidance_group || 'Unknown'}</td>
      <td><span className={`pill ${item.routed ? 'routed' : item.paid_route ? 'paid' : 'pending'}`}>{route(item)}</span></td>
      <td>{item.resolution || 'In progress / unknown'}</td>
      <td>{new Date(item.created_at).toLocaleTimeString()}</td>
    </tr>)}</tbody></table></div>
    {shown.length === 0 && <p className="empty-state">{loading ? 'Loading work items…' : 'No work items match this cohort or execution path.'}</p>}
    {more && <button className="more-button" type="button" onClick={onMore}>Load more</button>}
  </section>;
}
