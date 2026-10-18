'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { OutputPreview, WorkDetail } from '@/lib/contracts.generated';
import ExecutionTimeline from './ExecutionTimeline';
import GuidanceDetails from './GuidanceDetails';
import ResponseComparison from './ResponseComparison';

type Tab = 'exchange' | 'decision' | 'guidance' | 'timeline';

function tokenCount(value: number | null): string {
  return value === null ? 'Not reported' : value.toLocaleString('en-US');
}

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

export default function ExchangeInspector({ id, refreshKey, paused }: { id: string | null; refreshKey: number; paused: boolean }) {
  const [loadedDetail, setDetail] = useState<WorkDetail | null>(null);
  const detail = loadedDetail?.work_item.work_item_id === id ? loadedDetail : null;
  const [bodies, setBodies] = useState<Record<string, string | null>>({});
  const [previews, setPreviews] = useState<Record<string, OutputPreview | null>>({});
  const [tab, setTab] = useState<Tab>('exchange');
  const [loading, setLoading] = useState(false);
  const [clockNow, setClockNow] = useState(Date.now());
  const pendingBodies = useRef(new Set<string>());
  useEffect(() => {
    if (paused) return;
    setClockNow(Date.now());
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [paused]);
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

  const activeCalls = useMemo(() => detail ? detail.calls.filter(call =>
    !call.call_id.startsWith('gpt:') &&
    !detail.results.some(result => result.call_id === call.call_id && result.delivered_at)) : [],
  [detail]);
  useEffect(() => {
    if (!id || !detail) return;
    let cancelled = false;
    for (const call of activeCalls) {
      const key = `${id}:${call.call_id}`;
      api.outputPreview(id, call.call_id)
        .then(value => { if (!cancelled) setPreviews(old => ({ ...old, [key]: value })); })
        .catch(() => { if (!cancelled) setPreviews(old => ({ ...old, [key]: null })); });
    }
    return () => { cancelled = true; };
  }, [id, detail, activeCalls]);

  const localCall = detail?.calls.find(c => c.local_dispatch);
  const agentStatus = detail?.agent_statuses?.find(s => s.call_id === localCall?.call_id);
  const outputAge = agentStatus?.last_model_output_at ?
    Math.max(0, Math.floor((clockNow - new Date(agentStatus.last_model_output_at).getTime()) / 1000)) : null;
  const jobActive = !agentStatus?.timeout_reason && ['accepted', 'ollama_connecting', 'streaming'].includes(agentStatus?.agent_state || '');
  const outputTime = agentStatus?.last_model_output_at ?
    new Date(agentStatus.last_model_output_at).toLocaleString() : null;
  const localResult = detail?.results.find(r => r.call_id === localCall?.call_id);
  const parentCall = detail?.calls.find(c => c.call_id.startsWith('gpt:') && !c.parent_call_id);
  const paidResult = detail?.results.find(r => r.call_id !== localCall?.call_id &&
    r.call_id !== parentCall?.call_id && r.delivered_at);
  const effectiveGpt = detail?.dispositions.filter(d => d.stage === 'gpt' &&
    d.result_id === paidResult?.result_id &&
    !detail.dispositions.some(next => next.supersedes_id === d.disposition_id)) || [];
  const gptDisposition = effectiveGpt.length === 1 ? effectiveGpt[0] : null;
  const parentOutput = detail?.results.find(r => r.result_id === gptDisposition?.output_result_id) ||
    detail?.results.find(r => r.call_id === parentCall?.call_id && !!r.raw_content_id);
  const claudeCall = detail?.calls.find(c => c.call_id === localCall?.parent_call_id) ||
    detail?.calls.find(c => c.call_id === paidResult?.call_id && !!c.requested_model) ||
    detail?.calls.find(c => !c.local_dispatch && !!c.requested_model);
  const claudeInbound = detail?.messages.find(m => m.call_id === claudeCall?.call_id &&
    m.role === 'user' && m.direction === 'in');
  const claudeOutbound = detail?.messages.find(m => m.call_id === claudeCall?.call_id &&
    m.role === 'user' && m.direction === 'out');
  const claudeSystem = detail?.messages.find(m => m.call_id === claudeCall?.call_id &&
    m.role === 'system' && m.direction === 'out');
  const localPrompt = detail?.messages.find(m => m.call_id === localCall?.call_id &&
    m.role === 'user' && m.direction === 'out') ||
    detail?.messages.find(m => m.call_id === localCall?.call_id && m.role === 'user');
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
      <p>Requested: {primaryModel?.requested_model || 'Unknown'} · Reported: {primaryModel?.reported_model || 'Unknown'}</p>
      {agentStatus && <p>Last bridge observation · Skynet job: {agentStatus.agent_state || 'Unknown'} · Agent: {agentStatus.agent_reachable === true ? 'reachable' : agentStatus.agent_reachable === false ? 'unreachable' : 'unverified'} · Last Qwen output {outputAge === null ? 'not observed' : jobActive ? `${outputAge}s ago` : outputTime}{agentStatus.timeout_reason ? ` · ${agentStatus.timeout_reason}` : ''}</p>}</div>
      <div className="inspector-tabs" role="tablist" aria-label="Inspector views">
        {(['exchange', 'decision', 'guidance', 'timeline'] as Tab[]).map(value => <button key={value} type="button"
          role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{value}</button>)}</div>
      <div className="inspector-content">
        {tab === 'exchange' && <>
          {claudeCall && <>
            <BodyCard title="GPT → Claude original prompt"
              content={body(claudeInbound?.original_content_id || claudeOutbound?.original_content_id)}
              fallback={fallback(claudeInbound?.original_content_id || claudeOutbound?.original_content_id)} />
            <BodyCard title="Prompt sent to Claude"
              content={body(claudeOutbound?.effective_content_id || claudeInbound?.effective_content_id)}
              fallback={fallback(claudeOutbound?.effective_content_id || claudeInbound?.effective_content_id)} />
            {claudeSystem && <details className="instruction-details"><summary>Claude worker instructions</summary>
              <BodyCard title="Worker instruction content"
                content={body(claudeSystem.effective_content_id || claudeSystem.original_content_id)}
                fallback={fallback(claudeSystem.effective_content_id || claudeSystem.original_content_id)} />
            </details>}
          </>}
          {localCall && <>
            <BodyCard title="Claude → Skynet original prompt"
              content={body(localPrompt?.original_content_id)} fallback={fallback(localPrompt?.original_content_id)} />
            <BodyCard title="Effective prompt sent to Skynet"
              content={body(localPrompt?.effective_content_id)} fallback={fallback(localPrompt?.effective_content_id)} />
          </>}
          {!claudeCall && !localCall && <p className="muted">No prompt chain captured.</p>}
          {activeCalls.map(call => {
            const preview = previews[`${id}:${call.call_id}`];
            return <div key={call.call_id}>
              <BodyCard title={`${call.local_dispatch ? 'Qwen' : 'Claude'} live output (partial)`}
                content={preview?.text || null}
                fallback={preview ? 'No output captured yet.' : 'Loading captured output…'} />
              {preview && (preview.truncated || preview.missing_chunks > 0) &&
                <p className="coverage-note">Recent output preview: {preview.chunks_shown} chunk(s) shown
                  {preview.truncated ? ' · earlier captured text omitted from preview' : ''}
                  {preview.missing_chunks > 0 ? ` · ${preview.missing_chunks} chunk(s) unavailable` : ''}.
                </p>}
            </div>;
          })}
          <ResponseComparison local={body(localResult?.raw_content_id || localResult?.delivered_content_id)}
            downstream={body(paidResult?.delivered_content_id)}
            localModel={localModel?.reported_model || localModel?.requested_model || 'Unknown model'}
            downstreamModel={paidModel?.reported_model || paidModel?.requested_model || 'Paid model / unknown'} localState={fallback(localResult?.raw_content_id || localResult?.delivered_content_id)}
            downstreamState={fallback(paidResult?.delivered_content_id)} />
          <BodyCard title="Response delivered to caller" content={body(deliveredRef)} fallback={fallback(deliveredRef)} />
          {gptDisposition && <>
            <BodyCard title="Parent-submitted answer (reported)"
              content={body(parentOutput?.raw_content_id)}
              fallback={fallback(parentOutput?.raw_content_id)} />
            <p className="coverage-note">Final Codex answer not independently observed</p>
          </>}
          <section className="usage-section" aria-label="Model usage">
            <h3>Reported model usage</h3>
            {(detail.usage || []).length ? detail.usage.map(observation => {
              const model = detail.models.find(item =>
                item.model_execution_id === observation.model_execution_id);
              return <div className="fact-card" key={observation.observation_id}>
                <strong>Requested: {model?.requested_model || 'Unknown'}</strong>
                <p>Reported: {model?.reported_model || 'Unknown'}</p>
                <small>{observation.scope} · {observation.is_cumulative ? 'Cumulative' : 'Incremental'} · {observation.provenance}</small>
                <div className="usage-values">
                  <span>Input: {tokenCount(observation.input_tokens)}</span>
                  <span>Cache-read input: {tokenCount(observation.cache_read_input_tokens)}</span>
                  <span>Cache-creation input: {tokenCount(observation.cache_creation_input_tokens)}</span>
                  <span>Output: {tokenCount(observation.output_tokens)}</span>
                </div>
              </div>;
            }) : <p className="muted">No usage reported.</p>}
            <p className="coverage-note">Provider-reported counters are shown per observation; cumulative and incremental rows are not summed. Missing values are unknown, not zero.</p>
          </section>
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
          <p className="coverage-note">Text differences do not establish semantic acceptance. GPT acknowledgements are parent-reported; final Codex delivery is not independently observed.</p>
        </div>}
        {tab === 'guidance' && <GuidanceDetails detail={detail} />}
        {tab === 'timeline' && <ExecutionTimeline detail={detail} />}
      </div>
      <div className="inspector-footer">Claude disposition: <strong>{selectedDisposition?.judgment || 'Unreported'}</strong> · GPT disposition: <strong>{gptDisposition?.judgment || 'Unknown'}</strong>{gptDisposition && ' (parent-reported)'}</div>
    </>}
  </aside>;
}
