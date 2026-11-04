'use client';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { DesignWorkflowDetail, DesignWorkflowReport } from '@/lib/designMode';

const when = (value: string) => new Date(value).toLocaleString();

export default function DesignWorkflows({ active }: { active: boolean }) {
  const [report, setReport] = useState<DesignWorkflowReport | null>(null);
  const [project, setProject] = useState('');
  const [awaiting, setAwaiting] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const [detail, setDetail] = useState<DesignWorkflowDetail | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);

  function select(id: string | null) {
    selectedRef.current = id; setSelected(id); setDetail(null); setDetailError('');
  }
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (project) params.set('repository_id', project);
    if (awaiting) params.set('awaiting_my_decision', 'true');
    setLoading(true);
    api.designWorkflows(params, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setReport(value); setError('');
      if (!value.available || !value.decision_filter_configured) setAwaiting(false);
      if (selectedRef.current && (!value.available || !value.workflows.some(w => w.workflow_id === selectedRef.current))) select(null);
    }).catch(cause => {
      if (!controller.signal.aborted) setError((cause as Error).message);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, project, awaiting, refresh]);
  useEffect(() => {
    if (!active || !selected) return;
    const controller = new AbortController();
    api.designWorkflow(selected, controller.signal).then(value => {
      if (!controller.signal.aborted && selectedRef.current === selected) { setDetail(value); setDetailError(''); }
    }).catch(cause => {
      if (!controller.signal.aborted && selectedRef.current === selected) setDetailError((cause as Error).message);
    });
    return () => controller.abort();
  }, [active, selected, refresh]);
  useEffect(() => {
    if (!active) return;
    const refreshVisible = () => { if (!document.hidden) setRefresh(n => n + 1); };
    const interval = setInterval(refreshVisible, 10000);
    document.addEventListener('visibilitychange', refreshVisible);
    return () => { clearInterval(interval); document.removeEventListener('visibilitychange', refreshVisible); };
  }, [active]);

  return <section className="design-workflows cc-overview panel" aria-label="Design workflows">
    <div className="cc-heading"><div><h2>Design workflows</h2>
      <p>Inspect planning, review and decision history. Continue the workflow in its owning chat.</p></div></div>
    <div className="cc-filters">
      <label>Design project<select value={project} onChange={e => { select(null); setProject(e.target.value); }}>
        <option value="">All projects</option>
        {report?.projects.map(p => <option key={p.repository_id} value={p.repository_id}>{p.repository_id} · {p.project_root}</option>)}
      </select></label>
      <label className="design-decision-filter"><span><input type="checkbox" checked={awaiting} disabled={!report?.available || !report.decision_filter_configured}
        aria-describedby="design-decision-help" onChange={e => { select(null); setAwaiting(e.target.checked); }} /> Awaiting my decision</span></label>
      <button type="button" onClick={() => setRefresh(n => n + 1)} disabled={loading}>Refresh workflows</button>
    </div>
    <p id="design-decision-help" className="muted">Shown after Chat delivers a plan and review, until you accept, revise or reject.</p>
    {error && <p role="alert">{error}</p>}
    {report && !report.available && <p role="status">Design Mode tables are not migrated.</p>}
    {report?.available && !report.decision_filter_configured && <p role="status">Decision filter unavailable: configure a Design Mode owner.</p>}
    {loading && <p role="status">Refreshing workflows…</p>}
    <div className="cc-table-wrap"><table className="cc-table"><thead><tr>
      <th>Workflow / feature</th><th>Project</th><th>Iteration / outcome</th><th>Accepted / rejected / revisions</th><th>Actor / reason</th><th>Updated</th>
    </tr></thead><tbody>{report?.workflows.map(w => <tr key={w.workflow_id} className={selected === w.workflow_id ? 'selected' : ''}>
      <td><button type="button" onClick={() => select(w.workflow_id)}>{w.workflow_id}</button><small>{w.request_key}</small></td>
      <td>{w.repository_id}<small>{w.project_root}</small></td>
      <td>{w.current_iteration}<small>{w.outcome_label}</small></td>
      <td>{w.accepted_count} / {w.rejected_count} / {w.revision_count}</td>
      <td>{w.status_actor}<small>{w.status_reason || '—'}</small></td>
      <td>{when(w.updated_at)}</td>
    </tr>)}
    {report?.available && !report.workflows.length && <tr><td colSpan={6}>{awaiting ? 'No workflows awaiting your decision' : 'No workflows in this project.'}</td></tr>}
    </tbody></table></div>
    {detailError && <p role="alert">{detailError}</p>}
    {selected && !detail && !detailError && <p role="status">Loading workflow evidence…</p>}
    {detail && <section aria-label="Design workflow evidence">
      <h3>Workflow {detail.workflow_id}</h3>
      <button type="button" onClick={() => select(null)}>Close workflow</button>
      <p>{detail.outcome_label} · owner {detail.owner_id} · version {detail.version}</p>
      <EvidenceTable title="Timeline" rows={detail.hops}
        keys={['iteration', 'sequence', 'from_actor', 'to_actor', 'kind', 'attempt', 'state', 'reason', 'worker_call_id', 'decision_id', 'result_id', 'started_at', 'finished_at']} />
      <EvidenceTable title="Iterations" rows={detail.iterations} keys={['iteration', 'request_key', 'task_id', 'work_item_id', 'source_head', 'created_at']} />
      <EvidenceTable title="Artifacts · versioned references" rows={detail.artifacts} keys={['iteration', 'kind', 'sha256', 'bytes', 'created_at']} />
      <EvidenceTable title="Planner evidence" rows={detail.evidence} keys={['iteration', 'path', 'sha256', 'read_at']} />
      <EvidenceTable title="Delivery receipts" rows={detail.deliveries} keys={['iteration', 'receipt_id', 'package_sha256', 'delivered_at']} />
      <EvidenceTable title="Decision audit" rows={detail.audit} keys={['iteration', 'actor', 'action', 'detail', 'recorded_at']} />
    </section>}
  </section>;
}

function EvidenceTable({ title, rows, keys }: { title: string; rows: Record<string, unknown>[]; keys: string[] }) {
  return <><h3>{title}</h3><div className="cc-table-wrap"><table className="cc-table"><thead><tr>
    {keys.map(key => <th key={key}>{key.replaceAll('_', ' ')}</th>)}
  </tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{keys.map(key => <td key={key}>{
    row[key] == null ? '—' : typeof row[key] === 'object' ? JSON.stringify(row[key]) : String(row[key])
  }</td>)}</tr>)}
  {!rows.length && <tr><td colSpan={keys.length}>No recorded entries.</td></tr>}
  </tbody></table></div></>;
}
