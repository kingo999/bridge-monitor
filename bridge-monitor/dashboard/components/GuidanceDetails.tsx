'use client';

import type { WorkDetail } from '@/lib/contracts.generated';

type ManifestItem = { item_id?: string; kind?: string; sha256?: string; review_id?: string;
  source_work_item_id?: string; source_result_id?: string; reason?: string };
type Manifest = { state?: string; pack_id?: string; pack_version_id?: string;
  pack_sha256?: string; supplied?: ManifestItem[]; excluded?: ManifestItem[];
  selection_source?: string; byte_length?: number };

function display(value: unknown): string {
  return value == null || value === '' ? 'Unknown' : String(value);
}

export default function GuidanceDetails({ detail }: { detail: WorkDetail }) {
  const reviews = detail.example_reviews || [];
  const latestReview = (pack: string | undefined, item: string | undefined) =>
    [...reviews].reverse().find(review => review.pack_version_id === pack && review.example_id === item);
  const repair = detail.dispositions.filter(disposition =>
    !detail.dispositions.some(next => next.supersedes_id === disposition.disposition_id));
  return <section className="guidance-details" aria-label="Guidance details">
    <h3>Guidance</h3>
    {detail.decisions.length === 0 && <p className="muted">No guidance decision captured.</p>}
    {detail.decisions.map(decision => {
      const manifest = (decision.guidance_manifest || {}) as Manifest;
      const actuallySupplied = (detail.supplied_decision_ids || []).includes(decision.decision_id);
      return <div className="fact-card" key={decision.decision_id}>
        <strong>{display(manifest.state)} · {display(decision.route)} route</strong>
        <p>Pack {manifest.pack_id || 'none'} · version {manifest.pack_version_id || 'none'} · selection {display(manifest.selection_source)}</p>
        {manifest.pack_sha256 && <small>Registered hash {manifest.pack_sha256}</small>}
        <p>{actuallySupplied ? 'Supplied to Qwen' : 'Selected / not supplied'}: {manifest.supplied?.length || 0} · Excluded: {manifest.excluded?.length || 0}</p>
        {(manifest.supplied || []).map(item => {
          const review = latestReview(manifest.pack_version_id, item.item_id);
          return <div className="guidance-item" key={`supplied-${item.item_id}`}>
            <strong>{item.kind || 'item'} {display(item.item_id)}</strong> <span>{actuallySupplied ? 'supplied' : 'selected / not supplied'}</span>
            {item.sha256 && <small>Content hash {item.sha256}</small>}
            {item.kind === 'example' && <small>Reuse {review ? `${review.status} · ${review.permitted_use}` : 'unknown'} · review {item.review_id || 'missing'} · exposure {review?.exposure || 'unknown'}</small>}
            {item.source_result_id && <small>Source result {item.source_result_id} · work {display(item.source_work_item_id)}</small>}
          </div>;
        })}
        {(manifest.excluded || []).map((item, index) => <div className="guidance-item" key={`excluded-${index}`}>
          <strong>{display(item.item_id || item.reason)}</strong> <span>excluded · {display(item.reason)}</span>
          {item.item_id && (() => { const review = latestReview(manifest.pack_version_id, item.item_id); return review ?
            <small>Reuse {review.status} · {review.permitted_use} · {review.exposure} · review {review.review_id}</small> : null; })()}
        </div>)}
        <p>Gate: {display(decision.reason || decision.eligibility)}</p>
        <details><summary>Gate evidence and policy pin</summary><pre>{JSON.stringify(decision.gate_facts, null, 2)}</pre></details>
      </div>;
    })}
    <h3>Repair and validation</h3>
    {repair.map(disposition => {
      const facts = disposition.details || {};
      return <div className="fact-card" key={disposition.disposition_id}>
        <strong>{disposition.stage} · {disposition.judgment}</strong>
        <p>Failure {display(facts.failure_category)} · repair {display(facts.repair_effort)}</p>
        <small>Result {disposition.result_id} · correction execution {display((facts.correction_execution_ids as string[] | undefined)?.join(', '))}</small>
        {Array.isArray(facts.guidance_ids) && facts.guidance_ids.length > 0 && <small>Relevant guidance {facts.guidance_ids.join(', ')}</small>}
        {disposition.reason && <p>{disposition.reason}</p>}
      </div>;
    })}
    {repair.length === 0 && <p className="muted">No repair assessment reported.</p>}
    {(detail.evaluations || []).map(evaluation => <div className="fact-card" key={evaluation.evaluation_id}>
      <strong>Validation · result {evaluation.result_id}</strong>
      <pre>{JSON.stringify(evaluation.evidence, null, 2)}</pre>
    </div>)}
    {(detail.result_links || []).map(link => <p className="guidance-link" key={`${link.from_result_id}-${link.to_result_id}-${link.relation}`}>
      {link.from_result_id} → {link.to_result_id} · {link.relation}</p>)}
    {reviews.length > 0 && <details><summary>Human example reviews ({reviews.length})</summary>
      {reviews.map(review => <div className="guidance-item" key={review.review_id}>
        <strong>{review.example_id} · {review.status}</strong>
        <small>{review.permitted_use} · {review.exposure} · reviewer {review.reviewer_id} · {new Date(review.reviewed_at).toLocaleString()}</small>
        <small>Review {review.review_id} · source work {display(review.source_work_item_id)} · result {display(review.source_result_id)} · origin {display(review.source_origin)}</small>
        <pre>{JSON.stringify({ scope: review.scope, evidence: review.evidence }, null, 2)}</pre>
      </div>)}
    </details>}
    <p className="coverage-note">Missing assessments remain unknown. Guidance exposure and repair are recorded facts; they do not establish causation.</p>
  </section>;
}
