'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { WorkDetail } from '@/lib/contracts.generated';
import ExecutionTimeline from './ExecutionTimeline';
import ResponseComparison from './ResponseComparison';

type Tab = 'exchange' | 'decision' | 'timeline';

function BodyCard({ title, content, fallback }: { title: string; content: string | null; fallback: string }) {
  const [expanded, setExpanded] = useState(false);
  const [search, setSearch] = useState('');
  const display = search && content ? content.split('\n').filter(line => line.toLowerCase().includes(search.toLowerCase())).join('\n') : content;
  return <div className="body-card"><div className="body-card-title"><strong>{title}</strong>
    <input aria-label={`Search ${title}`} type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Find" />
    <button type="button" disabled={!content} onClick={() => content && navigator.clipboard.writeText(content)}>Copy</button>
    <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Collapse' : 'Expand'}</button></div>
    <pre className={expanded ? 'expanded' : ''}>{display ?? fallback}</pre></div>;
}

export default function ExchangeInspector({ id, refreshKey }: { id: string | null; refreshKey: number }) {
  const [loadedDetail, setDetail] = useState<WorkDetail | null>(null);
  const detail = loadedDetail?.work_item.work_item_id === id ? loadedDetail : null;
  const [bodies, setBodies] = useState<Record<string, string | null>>({});
  const [tab, setTab] = useState<Tab>('exchange');
  const [loading, setLoading] = useState(false);
  const pendingBodies = useRef(new Set<string>());
  useEffect(() => {
    if (!id) { setDetail(null); return; }
    let cancelled = false;
    setLoading(true);
    api.detail(id).then(value => { if (!cancelled) setDetail(value); })
      .catch(() => { if (!cancelled) setDetail(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id, refreshKey]);
  const references = useMemo(() => detail ? Array.from(new Set([
    ...detail.messages.flatMap(m => [m.original_content_id, m.effective_content_id]),
    ...detail.results.flatMap(r => [r.raw_content_id, r.delivered_content_id]),
  ].filter((value): value is string => !!value))) : [], [detail]);
  useEffect(() => {
    for (const ref of references) if (!(ref in bodies) && !pendingBodies.current.has(ref)) {
      pendingBodies.current.add(ref);
      api.content(ref).then(value => { setBodies(old => ({ ...old, [ref]: value })); })
        .catch(() => { setBodies(old => ({ ...old, [ref]: null })); })
        .finally(() => pendingBodies.current.delete(ref));
    }
  }, [references, bodies]);

  const localCall = detail?.calls.find(c => c.local_dispatch);
  const localResult = detail?.results.find(r => r.call_id === localCall?.call_id);
  const paidResult = detail?.results.find(r => r.call_id !== localCall?.call_id && r.delivered_at);
  const promptCallId = localCall?.call_id || paidResult?.call_id || detail?.calls[0]?.call_id;
  const original = detail?.messages.find(m => m.call_id === promptCallId && m.role === 'user');
  const localModel = detail?.models.find(m => m.call_id === localCall?.call_id);
  const paidModel = detail?.models.find(m => m.call_id === paidResult?.call_id);
  const primaryModel = localModel || paidModel;
  const deliveredRef = paidResult?.delivered_content_id || localResult?.delivered_content_id;
  const selectedDisposition = detail?.dispositions.find(d => d.result_id === localResult?.result_id && !detail.dispositions.some(next => next.supersedes_id === d.disposition_id));
  const body = (ref: string | null | undefined) => ref ? bodies[ref] ?? null : null;
  const fallback = (ref: string | null | undefined) => ref ?
    (ref in bodies ? `Content ${detail?.content_states[ref]?.completeness || 'unavailable'}` : 'Loading content…') : 'Not captured';

  return <aside className="panel inspector" aria-label="Selected work item">
    <div className="panel-heading"><div><h2>Selected work item</h2><p>{id || 'Choose a work item'}</p></div>
      {detail && <span className="pill paid">{detail.work_item.resolution || 'Outcome unknown'}</span>}</div>
    {!id && <p className="empty-state">Select a row to inspect prompts, responses and decisions.</p>}
    {id && loading && !detail && <p className="empty-state">Loading exchange…</p>}
    {detail && <><div className="inspector-summary"><h3>{detail.work_item.category || detail.work_item.work_item_id}</h3>
      <p>Requested: {primaryModel?.requested_model || 'Unknown'} · Reported: {primaryModel?.reported_model || 'Unknown'}</p></div>
      <div className="inspector-tabs" role="tablist" aria-label="Inspector views">
        {(['exchange', 'decision', 'timeline'] as Tab[]).map(value => <button key={value} type="button"
          role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{value}</button>)}</div>
      <div className="inspector-content">
        {tab === 'exchange' && <>
          <BodyCard title="Original prompt" content={body(original?.original_content_id)} fallback={fallback(original?.original_content_id)} />
          <BodyCard title="Effective prompt sent" content={body(original?.effective_content_id)} fallback={fallback(original?.effective_content_id)} />
          <ResponseComparison local={body(localResult?.raw_content_id || localResult?.delivered_content_id)}
            downstream={body(paidResult?.delivered_content_id)}
            localModel={localModel?.reported_model || localModel?.requested_model || 'Unknown model'}
            downstreamModel={paidModel?.reported_model || paidModel?.requested_model || 'Paid model / unknown'} localState={fallback(localResult?.raw_content_id || localResult?.delivered_content_id)}
            downstreamState={fallback(paidResult?.delivered_content_id)} />
          <BodyCard title="Response delivered to caller" content={body(deliveredRef)} fallback={fallback(deliveredRef)} />
        </>}
        {tab === 'decision' && <div className="decision-list">
          <h3>Routing and disposition</h3>
          {detail.decisions.map(d => <div className="fact-card" key={d.decision_id}><strong>{d.route} · {d.eligibility}</strong>
            <p>{d.reason || 'No reason recorded'}</p><small>Policy {d.policy_version || 'unknown'} · {new Date(d.decided_at).toLocaleString()}</small>
            <details><summary>Guidance and gate facts</summary><pre>{JSON.stringify({ guidance: d.guidance_manifest, gates: d.gate_facts }, null, 2)}</pre></details></div>)}
          {detail.dispositions.map(d => <div className="fact-card" key={d.disposition_id}><strong>{d.stage}: {d.judgment}</strong>
            <p>{d.reason || 'No reason recorded'}</p><small>Actor {d.actor_id} · result {d.result_id}</small></div>)}
          {detail.dispositions.length === 0 && <p className="muted">No disposition reported.</p>}
          <h3>Final resolution</h3>{detail.resolutions.length ? detail.resolutions.map(r => <p key={r.resolution_id}>{r.outcome} · {new Date(r.recorded_at).toLocaleString()}</p>) : <p className="muted">Unknown / pending</p>}
          <p className="coverage-note">Text differences do not establish semantic acceptance. GPT final survival requires its own acknowledgement.</p>
        </div>}
        {tab === 'timeline' && <ExecutionTimeline detail={detail} />}
      </div>
      <div className="inspector-footer">Claude disposition: <strong>{selectedDisposition?.judgment || 'Unreported'}</strong> · GPT disposition: <strong>{detail.dispositions.find(d => d.stage === 'gpt')?.judgment || 'Unknown'}</strong></div>
    </>}
  </aside>;
}
