'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { RouterAudit as Audit } from '@/lib/contracts.generated';
import ResponseComparison from './ResponseComparison';

export default function RouterAudit({ id, refreshKey }: { id: string; refreshKey: number }) {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [bodies, setBodies] = useState<Record<string, string | null>>({});
  useEffect(() => {
    let cancelled = false;
    api.routerAudit(id).then(value => { if (!cancelled) setAudit(value); })
      .catch(() => { if (!cancelled) setAudit(null); });
    return () => { cancelled = true; };
  }, [id, refreshKey]);
  const refs = audit ? [audit.prompt_refs.original, audit.prompt_refs.paid_effective,
    audit.prompt_refs.shadow_effective, audit.result_refs.paid,
    audit.result_refs.shadow_raw, audit.result_refs.shadow_parsed]
    .filter((ref): ref is string => !!ref) : [];
  useEffect(() => {
    let cancelled = false;
    for (const ref of refs) {
      if (Object.prototype.hasOwnProperty.call(bodies, ref)) continue;
      api.content(ref).then(body => { if (!cancelled) setBodies(old => ({ ...old, [ref]: body })); })
        .catch(() => { if (!cancelled) setBodies(old => ({ ...old, [ref]: null })); });
    }
    return () => { cancelled = true; };
  }, [refs.join('|'), bodies]);
  if (!audit || audit.work_item.work_item_id !== id) return <p className="muted">Loading router audit…</p>;
  if (!audit.parent_decision) return <p className="muted">No parent-router attempt recorded for this work item.</p>;
  const body = (ref: string | null) => ref ? bodies[ref] ?? null : null;
  return <section className="router-audit" aria-label="Parent router audit">
    <h3>Parent routing attempt</h3>
    <p>Decision: <strong>{audit.parent_decision.route}</strong> · {audit.parent_decision.reason || 'Unclassified'}</p>
    <p>Paid call: {audit.paid_call_id || 'None'} · Shadow Qwen: {audit.shadow_call_id || 'Not queued'}</p>
    {audit.jobs.map(job => <p key={job.call_id}>{job.execution_mode} {job.pool}: {job.state}
      {job.slot_held ? ' · capacity held for reconciliation' : ''} · {job.requested_model || 'Model unknown'}</p>)}
    <div className="audit-manifest"><h4>Pinned decision</h4><pre>{JSON.stringify(audit.parent_decision.gate_facts, null, 2)}</pre></div>
    <h3>Exact exchange</h3>
    <div className="body-card"><strong>Original GPT → Claude prompt</strong><pre>{body(audit.prompt_refs.original) ?? 'Not captured'}</pre></div>
    <div className="body-card"><strong>Effective paid prompt</strong><pre>{body(audit.prompt_refs.paid_effective) ?? 'Not captured'}</pre></div>
    <div className="body-card"><strong>Effective shadow prompt</strong><pre>{body(audit.prompt_refs.shadow_effective) ?? 'Not queued or not captured'}</pre></div>
    <ResponseComparison local={body(audit.result_refs.shadow_raw)}
      downstream={body(audit.result_refs.paid)} localModel="Qwen shadow · undelivered"
      downstreamModel="Paid Claude response" localState="No Qwen result captured"
      downstreamState="No paid response captured" />
    {audit.result_refs.shadow_parsed && <div className="body-card"><strong>Parsed candidate module</strong>
      <pre>{body(audit.result_refs.shadow_parsed) ?? 'Not captured'}</pre></div>}
    <h3>Independent evaluation</h3>
    {audit.evaluation ? <p><strong>{audit.evaluation.state}</strong> · {audit.evaluation.reason}
      {' · '}{audit.evaluation.verifier_image}</p> : <p className="muted">No independent evaluation recorded.</p>}
    {audit.evaluation_history.length > 1 && <details><summary>Evaluation corrections ({audit.evaluation_history.length})</summary>
      {audit.evaluation_history.map(item => <p key={item.evaluation_id}>
        {new Date(item.recorded_at).toLocaleString()} · {String(item.evidence.state || 'unknown')} · {String(item.evidence.reason || 'unclassified')}
      </p>)}</details>}
    <p className="coverage-note">Shadow output was never delivered to Claude. A trusted-test pass is separate from a paid-model or GPT acceptance judgment.</p>
    <details><summary>Router timeline and corrections</summary><ol className="timeline">
      {audit.timeline.map((event, index) => <li key={`${event.call_id}-${index}`}>
        {new Date(event.occurred_at).toLocaleString()} · {event.event_type} · {event.call_id || 'work item'}</li>)}
      {audit.dispositions.map(item => <li key={item.disposition_id}>
        {new Date(item.recorded_at).toLocaleString()} · {item.stage} {item.judgment}
        {item.supersedes_id ? ` · corrects ${item.supersedes_id}` : ''}</li>)}
    </ol></details>
  </section>;
}
