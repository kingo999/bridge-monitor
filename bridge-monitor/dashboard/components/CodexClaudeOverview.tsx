'use client';
import { useEffect, useState } from 'react';
import { api, type CohortFilters } from '@/lib/api';
import type { CodexClaudeReport, CodexClaudeRow } from '@/lib/contracts.generated';
import ModelBreakdownChart from './ModelBreakdownChart';

type Props = { filters: CohortFilters; refreshKey: string; onSelect: (id: string) => void; paused: boolean };
const unknown = (value: string | number | null | undefined) => value == null ? 'Unknown' : value;
export default function CodexClaudeOverview({ filters, refreshKey, onSelect, paused }: Props) {
  const [report, setReport] = useState<CodexClaudeReport | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [extra, setExtra] = useState({ category: '', project: '', model: '', profile: '', policy_digest: '', evidence: '' });
  const [selected, setSelected] = useState<CodexClaudeRow | null>(null);
  const [decision, setDecision] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (paused && report) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ origin: filters.origin, completed_only: String(filters.completedOnly), ...extra });
    if (filters.createdFrom) params.set('created_from', new Date(filters.createdFrom).toISOString());
    if (filters.createdUntil) params.set('created_until', new Date(filters.createdUntil).toISOString());
    setLoading(true);
    api.codexClaude(params, controller.signal).then(value => {
      if (!controller.signal.aborted) { setReport(value); setError(''); setLoading(false); }
    }).catch(cause => { if (!controller.signal.aborted) { setError(String(cause)); setLoading(false); } });
    return () => controller.abort();
  // report is deliberately not a dependency: replacing a report must not trigger another request.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.origin, filters.createdFrom, filters.createdUntil, filters.completedOnly, extra, refreshKey, paused]);
  useEffect(() => {
    setDecision(null);
    if (!selected?.decision_id) return;
    const controller = new AbortController();
    api.codexClaudeDecision(selected.work_item_id, controller.signal)
      .then(value => { if (!controller.signal.aborted) setDecision(value); }).catch(() => {});
    return () => controller.abort();
  }, [selected]);
  function exportHref(format: string) {
    const params = new URLSearchParams({ format });
    for (const [key, value] of Object.entries(report?.filters || {})) if (value != null && value !== '') params.set(key, String(value));
    if (report) params.set('as_of', report.as_of);
    return '/api/v1/codex-claude/export?' + params;
  }
  const time = (value: number | null | undefined) => value == null ? 'Unknown' : value.toFixed(1) + 's';
  return <section className="cc-overview panel" aria-label="Codex / Claude overview">
    <div className="cc-heading"><div><h2>Codex / Claude overview</h2><p>Who handled each workflow, why it was routed, and what the evidence shows.</p></div>
      <div className="cc-export">{report && !loading && !error && <><a href={exportHref('jsonl')}>Export JSONL</a><a href={exportHref('csv')}>Export CSV</a></>}</div></div>
    {error && <p className="notice-data-error" role="status">Report unavailable: {error}</p>}
    <div className="cc-stats">
      <div><strong>{report?.total ?? '—'}</strong><span>Observed workflows</span></div>
      <div><strong>{report?.outcomes.parent_accepted ?? '—'}</strong><span>Parent accepted</span></div>
      <div><strong>{report?.outcomes.rework ?? '—'}</strong><span>Revised or replaced</span></div>
      <div><strong>{time(report?.latency_seconds.median)}</strong><span>Median duration · n={report?.latency_seconds.samples ?? 0}</span></div>
      <div><strong>{time(report?.latency_seconds.p95)}</strong><span>p95 duration</span></div>
      <div><strong>{report ? '$' + Number(report.cost.known_subtotal).toFixed(4) : 'Unknown'}</strong><span>Known API-equivalent subtotal</span></div>
    </div>
    <div className="cc-explanation"><p>{report?.scope_note || 'Missing measurements stay unknown; completion is separate from verified correctness.'}</p>
      <p>Cost evidence: {report?.cost.known_workflows ?? 0} workflow(s) with receipts · {report?.cost.unknown_workflows ?? 0} unknown.
        {' '}Checks passed: {unknown(report?.outcomes.checks_passed)} · Human acceptance: {unknown(report?.outcomes.human_accepted)}</p>
      <p>Spending and token usage are statistics. The $10 daily and 80% allowance notices do not block work.</p>
      <p className="cc-readiness">{report?.readiness.reason || 'Automatic routing remains inactive pending explicit rollout approval.'}</p></div>
    {report ? <ModelBreakdownChart models={report.models} /> : <p className="muted">Loading model breakdown…</p>}
    <div className="cc-filters">{Object.entries(extra).map(([key, value]) => <label key={key}>
      {({ category: 'Task kind', project: 'Project', model: 'Model', profile: 'Profile', policy_digest: 'Policy digest', evidence: 'Evidence' } as Record<string,string>)[key]}
      <input value={value} onChange={event => setExtra(current => ({ ...current, [key]: event.target.value }))} /></label>)}</div>
    <div className="cc-table-wrap"><table className="cc-table"><thead><tr><th>Workflow</th><th>Executor / model</th><th>Route reason</th>
      <th>Complexity / override</th><th>Outcome evidence</th><th>Duration</th><th>API-equivalent</th></tr></thead><tbody>
      {report?.rows.map(row => <tr key={row.work_item_id} className={selected?.work_item_id === row.work_item_id ? 'selected' : ''}>
        <td><button type="button" onClick={() => { setSelected(row); if (row.task_id) onSelect(row.work_item_id); }}>{row.work_item_id}</button>
          <small>{row.category} · {new Date(row.created_at).toLocaleString()}</small></td>
        <td>{row.executor}<small>Reported: {unknown(row.reported_model)}</small><small>Requested: {unknown(row.requested_model)}</small></td>
        <td>{row.route_reason}<small>{row.profile || 'Profile unknown'}</small></td>
        <td>{unknown(row.complexity)}<small>Override: {unknown(row.override)}</small><small>Risk: {unknown(row.risk)}</small></td>
        <td>{row.evidence_status}<small>{row.status}{row.rework ? ' · Rework' : ''}</small></td>
        <td>{time(row.duration_seconds)}</td><td>{row.cost_usd_equivalent == null ? 'Unknown' : '$' + Number(row.cost_usd_equivalent).toFixed(4)}</td>
      </tr>)}
      {!report?.rows.length && <tr><td colSpan={7}>{loading ? 'Loading workflows…' : 'No Codex / Claude workflows in this cohort.'}</td></tr>}
    </tbody></table></div>
    {selected && <details className="cc-decision" open><summary>Routing and usage evidence · {selected.work_item_id}</summary>
      <p>Policy digest: {unknown(selected.policy_digest)} · Receipt hashes: {selected.receipt_hashes.join(', ') || 'Unknown'}</p>
      <p>Scoped tokens: {Object.entries(selected.usage).map(([key, value]) => key + ': ' + unknown(value)).join(' · ')}</p>
      {decision ? <pre>{JSON.stringify(decision, null, 2)}</pre> : <p>{selected.decision_id ? 'Loading routing record…' : 'No versioned routing decision recorded for this historical workflow.'}</p>}
    </details>}
    <p className="cc-definition">{report?.definition_version || 'codex-claude-v1'} · As of {report ? new Date(report.as_of).toLocaleString() : '—'}</p>
  </section>;
}
