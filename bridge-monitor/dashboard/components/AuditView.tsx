'use client';
import HandoffAudit from './HandoffAudit';
import { useEffect, useState } from 'react';
import { auditRequest, type AuditCoverage, type AuditLaunch, type AuditPage, type AuditRow, type AuditView as View } from '@/lib/audit';

const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : 'Unknown';
const initial = { range: 'recent', q: '', mode: 'words', sort: 'time', direction: 'desc',
  project: '', handler: '', status: '', usage: '', layer: '', kind: '', run: '', attribution: '', destination: '', port: '', from: '', until: '' };

export function AuditStatus({ coverage }: { coverage: AuditCoverage }) {
  const stale = coverage.synced_at && Date.now() - Date.parse(coverage.synced_at) > 180000;
  return <div className="audit-coverage" role="status">
    <p>{coverage.scope} · Last successful sync {date(coverage.synced_at)}{stale ? ' · Last success is old' : ''}</p>
    <p>Last health signal processed {date(coverage.health_processed_at)} <small>(consumer time)</small></p>
    <p>Signals can be delayed or replayed. Host capture coverage remains unknown between recorded signals.</p>
    <p>{coverage.indexed.toLocaleString()} observations indexed · {coverage.invalid} invalid metadata records
      {!coverage.complete && ' · Indexing incomplete — results may omit records'}
      {coverage.execution_pending > 0 && ` · ${coverage.execution_pending} workflows waiting for indexing`}</p>
    <p>Counts represent stored observations. Source replay is possible. Gap timestamps record when gaps were reported.</p>
  </div>;
}

export default function AuditView({ launch, visible }: { launch: AuditLaunch | null; visible: boolean }) {
  const [view, setView] = useState<AuditLaunch['view']>('executions');
  const [navigation, setNavigation] = useState<AuditLaunch | null>(launch);
  function navigate(next: AuditLaunch) { setNavigation(next); setView(next.view); }
  useEffect(() => { if (launch) navigate(launch); }, [launch]);
  return <section className="audit panel" aria-label="Audit history">
    <h2>Audit</h2>
    <div className="audit-view-controls" role="group" aria-label="Audit history type">
      {(['executions', 'network'] as View[]).map(v => <button key={v} type="button" aria-pressed={view === v}
        onClick={() => setView(v)}>{v === 'executions' ? 'Executions' : 'Network activity'}</button>)}
      <button type="button" aria-pressed={view === 'handoffs'} onClick={() => setView('handoffs')}>Design Mode handoffs</button>
    </div>
    {visible && view === 'handoffs' && <HandoffAudit />}
    <AuditList view="executions" active={visible && view === 'executions'} launch={navigation?.view === 'executions' ? navigation : null}
      onNavigate={navigate} />
    <AuditList view="network" active={visible && view === 'network'} launch={navigation?.view === 'network' ? navigation : null}
      onNavigate={navigate} />
  </section>;
}

function AuditList({ view, active, launch, onNavigate }: { view: View; active: boolean; launch: AuditLaunch | null; onNavigate: (next: AuditLaunch) => void }) {
  const [filters, setFilters] = useState(initial);
  const [rangeChosen, setRangeChosen] = useState(false);
  const [cohort, setCohort] = useState<string | undefined>();
  const [page, setPage] = useState<AuditPage | null>(null);
  const [history, setHistory] = useState<number[]>([0]);
  const [selected, setSelected] = useState<AuditRow | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [coverage, setCoverage] = useState<AuditCoverage | null>(null);
  const [refresh, setRefresh] = useState(0);
  async function open(token: string, after = 0) {
    const result = await auditRequest<AuditPage>(`snapshots/${encodeURIComponent(token)}?after=${after}`);
    setPage(result); setSelected(null);
  }
  async function search(values = filters, base: string | null = cohort || null) {
    setLoading(true); setError('');
    try {
      const adjusted = { ...values };
      if (adjusted.q && !rangeChosen && adjusted.range === 'recent') adjusted.range = 'all';
      setFilters(adjusted);
      const result = await auditRequest<{ token: string }>('search', {
        ...adjusted, view, ...(base ? { cohort_token: base } : {}),
        from: adjusted.from ? new Date(adjusted.from).toISOString() : '',
        until: adjusted.until ? new Date(adjusted.until).toISOString() : '',
      });
      await open(result.token); setHistory([0]);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setLoading(false); }
  }
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const load = () => auditRequest<AuditCoverage>('status', undefined, controller.signal)
      .then(setCoverage).catch(() => {});
    void load();
    const interval = setInterval(load, 15000);
    return () => { controller.abort(); clearInterval(interval); };
  }, [active, refresh]);
  useEffect(() => {
    if (!active || page || launch) return;
    void search();
  // Search state is explicitly submitted, so keystrokes never rerun a query.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
  useEffect(() => {
    if (!launch) return;
    const values = { ...initial, range: 'all', usage: launch.usage || '', run: launch.run || '', q: launch.q || '', mode: launch.q ? 'exact' : 'words' };
    setFilters(values); setRangeChosen(true); setCohort(launch.token);
    void search(values, launch.token);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [launch]);
  const update = (key: keyof typeof filters, value: string) => setFilters(f => ({ ...f, [key]: value }));
  const input = (key: keyof typeof filters, label: string) => <label>{label}<input value={filters[key]}
    onChange={e => update(key, e.target.value)} /></label>;
  const choose = (key: keyof typeof filters, label: string, options: [string, string][]) =>
    <label>{label}<select value={filters[key]} onChange={e => {
      update(key, e.target.value); if (key === 'range') setRangeChosen(true);
    }}>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>;
  async function navigate(after: number, backwards = false) {
    if (!page) return;
    setLoading(true); setError('');
    try { await open(page.token, after); setHistory(h => backwards ? h.slice(0, -1) : [...h, after]); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }
  return <div hidden={!active}>
    {cohort && <div className="audit-cohort">Captured overview cohort · filters use historical captured values.
      <button type="button" onClick={() => { setCohort(undefined); void search({ ...initial, range: 'all' }, null); }}>Search all workflows</button>
      {page?.filters.as_of != null && <span> · As of {date(String(page.filters.as_of))}</span>}</div>}
    {coverage && <AuditStatus coverage={coverage} />}
    <form className="audit-filters" onSubmit={e => { e.preventDefault(); void search(); }}>
      {choose('range', 'Search range', [['recent', 'Recent · 24 hours'], ['all', 'All retained'], ['custom', 'Custom range']])}
      {filters.range === 'custom' && <>{(['from', 'until'] as const).map(k => <label key={k}>{k === 'from' ? 'From' : 'Until'}
        <input type="datetime-local" value={filters[k]} onChange={e => update(k, e.target.value)} /></label>)}</>}
      {input('q', 'Search audit')}
      {choose('mode', 'Match', view === 'network' ? [['words', 'Metadata words'], ['exact', 'Exact ID / IP'],
        ['process', 'Process prefix'], ['requested', 'Requested hostname prefix'], ['alias', 'Exact passive DNS alias']] :
        [['words', 'Metadata words'], ['exact', 'Exact identifier']])}
      {view === 'executions' ? <>
        {input('project', 'Project')}
        {choose('handler', 'Handler', [['', 'All handlers'], ['codex', 'Direct Codex'], ['claude', 'Delegated Claude'], ['not_dispatched', 'Not dispatched']])}
        {input('status', 'Status')}
        {choose('usage', 'Measured usage', [['', 'Any coverage'], ['yes', 'Measured counters'], ['no', 'Counters unknown']])}
      </> : <>
        {choose('layer', 'Layer', [['', 'All layers'], ['host', 'Host'], ['application', 'Application'], ['collector', 'Collector']])}
        {choose('kind', 'Event kind', [['', 'All events'], ...['attempt', 'connected', 'transfer', 'completed', 'failed', 'gap', 'health'].map(k => [k, k] as [string, string])])}
        {input('run', 'Recorded run')}
        {input('destination', 'Exact destination')}
        {input('port', 'Destination port')}
        {choose('attribution', 'Attribution', [['', 'All attribution'], ['application', 'Application context'], ['registered', 'Registered'],
          ['process_lineage', 'Process lineage'], ['none', 'Unattributed']])}
      </>}
      {choose('sort', 'Sort by', view === 'executions' ? [['time', 'Time'], ['project', 'Project'], ['status', 'Status']] :
        [['time', 'Time'], ['destination', 'Destination'], ['bytes', 'Observed sent bytes']])}
      {choose('direction', 'Direction', [['desc', 'Descending'], ['asc', 'Ascending']])}
      <button type="submit" disabled={loading}>Apply search</button>
      <button type="button" disabled={loading} onClick={() => { setRefresh(n => n + 1); void search(); }}>Refresh results</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {page && coverage && (coverage.indexed_high > page.coverage.indexed_high ||
      (coverage.execution_version || 0) > (page.coverage.execution_version || 0)) &&
      <p role="status">New indexed records are available. Refresh results to include them.</p>}
    {view === 'network' && filters.run && <p>Recorded run scope · {filters.run}
      <button type="button" onClick={() => void search({ ...initial, range: 'all' }, null)}>Open all host activity</button></p>}
    <p aria-live="polite">{loading ? 'Loading audit history…' : page ? `${page.total.toLocaleString()} matches · result snapshot (up to 30 minutes)` : ''}
      {page && view === 'network' && !page.coverage.complete && ' · Snapshot indexing was incomplete'}
      {page && view === 'executions' && page.coverage.execution_pending > 0 && ' · Snapshot workflow indexing was incomplete'}</p>
    {page && <div className="cc-table-wrap"><table className="cc-table"><thead><tr>
      <th>Time / record</th>{view === 'executions' ? <><th>Project</th><th>Handler / status</th><th>Usage evidence</th></> :
        <><th>Process / event</th><th>Destination / attribution</th><th>Observed sent bytes</th></>}
    </tr></thead><tbody>{page.rows.map(row => <tr key={row.id}>
      <td><button type="button" onClick={() => setSelected(row)}>{row.id}</button><small>{date(row.time)}</small></td>
      {view === 'executions' ? <><td>{row.project || 'Unknown'}</td><td>{row.handler} · {row.status || 'Unknown'}</td>
        <td>{(row.has_usage ?? row.usage) ? 'Measured counters' : 'Unknown'}</td></> :
        <><td>{row.process || 'Unknown'}<small>{row.layer} · {row.kind}{row.error && ' · Invalid metadata'}</small></td>
          <td>{row.destination || 'Unknown'}<small>{row.attribution || 'Unknown'}</small></td><td>{row.bytes == null ? 'Unknown' : row.bytes}</td></>}
    </tr>)}
    {!page.rows.length && <tr><td colSpan={4}>No matching indexed records in this snapshot.</td></tr>}
    </tbody></table></div>}
    <div className="audit-pagination">
      <button type="button" disabled={loading || history.length < 2} onClick={() => void navigate(history[history.length - 2], true)}>Previous page</button>
      <button type="button" disabled={loading || page?.next == null} onClick={() => page?.next != null && void navigate(page.next)}>Next page</button>
    </div>
    {selected && <section className="audit-detail" aria-label="Audit evidence detail">
      <button type="button" onClick={() => setSelected(null)}>Close evidence</button>
      <h3>Evidence · {selected.id}</h3>
      {view === 'executions' && <p>Network attribution for direct desktop runs is unavailable. Recorded call IDs can be searched in Network activity.</p>}
      {view === 'executions' && selected.handler !== 'codex' && Array.isArray(selected.data.calls) &&
        selected.data.calls.map(call => <button type="button" key={String(call)} onClick={() =>
          onNavigate({ key: Date.now(), view: 'network', run: String(call) })}>Network observations · {String(call)}</button>)}
      {view === 'network' && selected.data.linked_work_item != null && <p>Verified Bridge workflow {String(selected.data.linked_work_item)} · call {String(selected.data.linked_call)}</p>}
      {view === 'network' && selected.data.linked_work_item != null && <button type="button" onClick={() =>
        onNavigate({ key: Date.now(), view: 'executions', q: String(selected.data.linked_work_item) })}>Open linked execution</button>}
      <dl>{Object.entries(selected.data).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt>
        <dd>{value == null ? 'Unknown' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}</dd></div>)}</dl>
    </section>}
  </div>;
}
