import type { ShadowCohort as Cohort } from '@/lib/contracts.generated';
import type { RouterFilters } from '@/lib/api';

export default function ShadowCohort({ report, filters, onChange }: {
  report: Cohort | null; filters: RouterFilters; onChange: (next: RouterFilters) => void;
}) {
  if (!report) return null;
  const entries = Object.entries(report.reasons).sort((a, b) => b[1] - a[1]);
  return <section className="panel shadow-cohort" aria-label="Parent router shadow cohort">
    <div className="panel-heading"><div><h2>Parent router · shadow audit</h2>
      <p>Attempted tasks, independent verification, and usage coverage</p></div>
      <span className="pill">{report.definition_version}</span></div>
    <div className="shadow-filters">
      <label>Model profile<input value={filters.modelProfile} aria-label="Router model profile"
        onChange={event => onChange({ ...filters, modelProfile: event.target.value })} /></label>
      <label>Prompt version<input value={filters.promptVersion} aria-label="Router prompt version"
        onChange={event => onChange({ ...filters, promptVersion: event.target.value })} /></label>
      <label>Verifier reference<input value={filters.verifierRef} aria-label="Router verifier reference"
        onChange={event => onChange({ ...filters, verifierRef: event.target.value })} /></label>
    </div>
    <div className="shadow-stats">
      <div><strong>{report.attempted_tasks}</strong><span>Router attempts</span></div>
      <div><strong>{report.queued_shadow}</strong><span>Qwen shadow queued</span></div>
      <div><strong>{report.independent_evaluated}/{report.queued_shadow}</strong><span>Independently evaluated</span></div>
      <div><strong>{report.outcomes.pass || 0}</strong><span>Trusted-test passes</span></div>
      <div><strong>{report.outcomes.fail || 0}</strong><span>Trusted-test failures</span></div>
      <div><strong>{report.outcomes.unknown || 0}</strong><span>Unknown outcomes</span></div>
    </div>
    <div className="shadow-detail-row">
      <p>Reasons: {entries.length ? entries.map(([reason, count]) => `${reason} ${count}`).join(' · ') : 'None recorded'}</p>
      <p>Token usage reported: Qwen {report.usage_coverage.qwen}/{report.queued_shadow} · paid {report.usage_coverage.paid}/{report.attempted_tasks}</p>
      <p>Median queue: {report.queue_latency_ms.median ?? 'Unknown'} ms · Qwen execution: {report.shadow_execution_latency_ms.median ?? 'Unknown'} ms · verification: {report.verifier_latency_ms.median ?? 'Unknown'} ms</p>
      <p>Conditional avoided paid calls: {report.conditional_avoided_paid_calls ?? 'Unknown'} · Billed savings: {report.billed_savings ?? 'Unknown'}</p>
    </div>
  </section>;
}
