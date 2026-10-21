import type { RuntimeExecution } from '@/lib/contracts.generated';

export function RuntimeSummary({ execution, onReceipt }: {
  execution: RuntimeExecution; onReceipt: () => void;
}) {
  const latest = execution.receipts[0];
  const reconciled = execution.state === 'terminated' && latest?.observed_state === 'terminated';
  return <section className="runtime-summary" aria-label="Recorded execution state">
    <p><strong>{reconciled ? 'Reconciled' : 'Recorded execution state'}: {execution.state}</strong></p>
    <p>Slot: {execution.slot_held ? 'Held' : 'Released'}</p>
    {latest && <button type="button" onClick={onReceipt}>View reconciliation receipt</button>}
  </section>;
}

export default function ReconciliationDetails({ execution }: { execution: RuntimeExecution }) {
  return <section aria-label="Reconciliation receipts">
    <h3>Reconciliation receipts</h3>
    <p className="receipt-value">Call: {execution.call_id}</p>
    {execution.receipts.map(receipt => <details className="fact-card" key={receipt.reconciliation_id}>
      <summary className="receipt-value">Reconciliation receipt {receipt.reconciliation_id}</summary>
      <p>Recorded observation: {receipt.observed_state}</p>
      <p>Recorded at: {new Date(receipt.recorded_at).toLocaleString()}</p>
      <p className="receipt-value">Actor: {receipt.actor_kind} · {receipt.actor_id}</p>
      {receipt.evidence.observed_at && <p>Evidence observed at: {receipt.evidence.observed_at}</p>}
      {receipt.evidence.kind && <p>Evidence type: {receipt.evidence.kind}</p>}
      {receipt.evidence.provenance && <p className="receipt-value">Provenance: {receipt.evidence.provenance}</p>}
      {receipt.evidence.receipt_id && <p className="receipt-value">Evidence receipt: {receipt.evidence.receipt_id}</p>}
      {receipt.evidence.basis && <p className="receipt-value">Basis: {receipt.evidence.basis}</p>}
      {typeof receipt.evidence.terminated === 'boolean' && <p>Termination attested: {receipt.evidence.terminated ? 'Yes' : 'No'}</p>}
      <p>Runner PID: {receipt.evidence.historical_runner_pid ?? 'Unknown'}</p>
      {receipt.evidence.source_sha256 && <p className="receipt-value">Source SHA-256: {receipt.evidence.source_sha256}</p>}
      {(receipt.evidence.source_evidence_path || receipt.evidence.source_path) && <p className="receipt-value">Source receipt: {receipt.evidence.source_evidence_path || receipt.evidence.source_path}</p>}
    </details>)}
    {!execution.receipts.length && <p className="muted">No reconciliation receipt recorded.</p>}
    {execution.receipts_truncated && <p className="coverage-note">Showing the latest {execution.receipts.length} receipts; earlier receipts omitted.</p>}
    <p className="coverage-note">Recorded operator or system evidence. Historical timeout observations and the original work outcome remain below.</p>
  </section>;
}
