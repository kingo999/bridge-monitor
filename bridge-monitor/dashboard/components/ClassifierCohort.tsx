'use client';

import { useEffect, useState } from 'react';
import { api, AuthenticationRequired } from '@/lib/api';
import type { ClassifierCohort as Report, ClassifierFilters, Dist } from '@/lib/api';
import CollapsiblePanel from './CollapsiblePanel';

const ms = (d: Dist) => d.samples ? `${d.median} / ${d.p95} ms (n=${d.samples})` : 'Unknown';
const list = (values: Record<string, number>) => {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  return entries.length ? entries.map(([k, v]) => `${k} ${v}`).join(' · ') : 'None recorded';
};

/** Advisory Gemma classifier shadow. Self-contained so a failure here never affects other panels. */
export default function ClassifierCohort({ paused = false }: { paused?: boolean }) {
  const [filters, setFilters] = useState<ClassifierFilters>({ originKind: 'all', model: '', profileId: '' });
  const [report, setReport] = useState<Report | null>(null);
  useEffect(() => {
    if (paused) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const value = await api.classifierCohort(filters, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]));
        if (!controller.signal.aborted) setReport(value);
      } catch (error) {
        if (!controller.signal.aborted && !(error instanceof AuthenticationRequired)) setReport(null);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [filters, paused]);
  if (!report || !report.available) return null;
  const c = report.counts;
  return <CollapsiblePanel className="shadow-cohort classifier-cohort" label="Gemma classifier shadow cohort"
    title="Gemma classifier · advisory shadow" subtitle={report.scope_note} badge={report.definition_version}>

    <div className="shadow-filters">
      <label>Capture<select value={filters.captureMethod || 'all'} aria-label="Classifier capture method"
        onChange={event => setFilters({ ...filters, captureMethod: event.target.value })}>
        {['all', 'manual', 'route_work', 'bridge_ingress'].map(m => <option key={m}>{m}</option>)}</select></label>
      <label>Origin<select value={filters.originKind} aria-label="Classifier origin"
        onChange={event => setFilters({ ...filters, originKind: event.target.value })}>
        {['all', 'natural', 'controlled', 'synthetic', 'replay'].map(o => <option key={o}>{o}</option>)}</select></label>
      <label>Model<select value={filters.model} aria-label="Classifier model"
        onChange={event => setFilters({ ...filters, model: event.target.value })}>
        <option value="">all</option>{report.filters.models.map(m => <option key={m}>{m}</option>)}</select></label>
      <label>Prompt/profile<select value={filters.profileId} aria-label="Classifier profile"
        onChange={event => setFilters({ ...filters, profileId: event.target.value })}>
        <option value="">all</option>{report.filters.profiles.map(p => <option key={p}>{p}</option>)}</select></label>
    </div>
    <div className="shadow-stats">
      <div><strong>{c.submitted}</strong><span>Submitted</span></div>
      <div><strong>{c.attempted}</strong><span>Attempted</span></div>
      <div><strong>{c.completed}</strong><span>Completed</span></div>
      <div><strong>{c.failed}</strong><span>Failed</span></div>
      <div><strong>{c.skipped}</strong><span>Skipped</span></div>
      <div><strong>{c.unknown_outcome}</strong><span>Unknown outcome</span></div>
    </div>
    <div className="shadow-detail-row">
      <p>Recommended routes: {list(report.recommendations.routes)}</p>
      {report.capture && <p>Captures: {list(report.capture.observations)} · {report.capture.unique_work_items} unique work items · {report.capture.overlap_work_items} shared by manual and hook captures. {report.capture.note}</p>}
      <p>Policy-blocked recommendations: {report.policy.blocked} of {report.policy.allowed + report.policy.blocked} · {list(report.policy.block_reasons)}</p>
      {report.policy.unknown_code_capacity_blocks !== undefined && <p>Unknown code capacity blocks: {report.policy.unknown_code_capacity_blocks} · Recommendations with other blocks: {report.policy.other_blocked} (counts may overlap). Unknown capacity is not evidence of an incorrect raw route.</p>}
      {report.fact_coverage?.code_available && <p>Code capacity facts: {list(report.fact_coverage.code_available)}</p>}
      <p>Reference labels: {report.reference.labelled} labelled · raw route acceptable {report.reference.raw_route_acceptable} · allowed and acceptable {report.reference.allowed_and_acceptable}</p>
      <p>Actual route known for {report.actual_route.known}/{c.completed} · differs from recommendation {report.actual_route.differs_from_recommendation} (a difference is not an error)</p>
      <p>Verification or correction on {report.follow_up.with_verification_or_correction}/{report.follow_up.completed} · operator judgment {report.follow_up.with_operator_judgment}</p>
      {report.router_evidence && <p>Router context for {report.router_evidence.completed_with_work_item} linked work item(s) · Qwen shadow evaluation {list(report.router_evidence.qwen_shadow_independent_evaluation)} · paid result dispositions {list(report.router_evidence.paid_result_dispositions)} (context, not a correctness judgment)</p>}
      <p>Skipped: {list(report.skip_reasons)} · Errors: {list({ ...report.error_types, ...report.contract_errors })}</p>
      {report.capture_exclusions?.available && <p>Excluded before classification: {report.capture_exclusions.total} · {list(report.capture_exclusions.reasons)} · Size measurements: {list(report.capture_exclusions.size_kinds)}. {report.capture_exclusions.note}</p>}
      <p>Median / p95 — queue {ms(report.latency_ms.queue)} · request {ms(report.latency_ms.request)} · end to end {ms(report.latency_ms.end_to_end)}</p>
      <p>Tokens in/out {report.tokens.input_total}/{report.tokens.output_total} · coverage {list(report.tokens.provenance)} · backend {list(report.backend_residency)}</p>
      <p>Local provider charge {report.cost.local_provider_charge_usd ?? 'Unknown'} · Electricity {report.cost.electricity_usd ?? 'Unknown'} ({report.cost.electricity_reason}) · Avoided paid calls {report.cost.hypothetical_avoided_paid_calls ?? 'Unknown'} · Billed savings {report.cost.billed_savings ?? 'Unknown'}</p>
    </div>
  </CollapsiblePanel>;
}
