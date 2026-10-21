import type { WorkDetail } from '@/lib/contracts.generated';

const tokens = (value: number | null | undefined) => value == null ? 'Unknown' : value.toLocaleString('en-US');

export default function DirectExecutionDetails({ detail }: { detail: WorkDetail }) {
  const direct = detail.direct_execution;
  if (!direct) return null;
  return <section className="decision-list" aria-label="Direct Codex execution">
    <h3>Direct Codex execution</h3>
    <p>{direct.summary}</p>
    <p><strong>Bypass reason: {direct.bypass_reason}</strong>{direct.reason && ` · ${direct.reason}`}</p>
    <p>Project: {direct.project_root}</p>
    <p>Session: {direct.session_id} · Status: {direct.status}</p>
    <p>Requested model: {direct.requested_model || 'Unknown'} · Reported model: Unknown · Provider: Unknown</p>
    <p className="coverage-note">Execution metadata is caller-reported. Recording does not authorize direct execution.</p>
    <h3>Direct Codex token usage</h3>
    {detail.direct_usage?.length ? detail.direct_usage.map(usage =>
      <div className="fact-card" key={usage.observation_id}>
        <strong>Codex CLI receipt · {usage.scope}</strong>
        <p>Source turn: {usage.source_turn_id}</p>
        <div className="usage-values">
          <span>Input: {tokens(usage.input_tokens)}</span>
          <span>Output: {tokens(usage.output_tokens)}</span>
          <span>Cache-read input: {tokens(usage.cache_read_input_tokens)}</span>
          <span>Cache-creation input: {tokens(usage.cache_creation_input_tokens)}</span>
          <span>Reasoning output: {tokens(usage.evidence.reasoning_output_tokens)}</span>
        </div>
        <small>Provenance: {usage.provenance} · Receipt SHA-256: {usage.evidence.source_sha256}</small>
      </div>) : <p className="muted">Token usage: Unknown. No CLI usage receipt imported.</p>}
    <p className="coverage-note">Counters cover the selected CLI turn, including its context and orchestration. They are not per-tool costs. Cached and reasoning tokens may be subsets; counters are not added together. No cost is inferred. Desktop usage stays unknown without a supported receipt. Imported receipts are caller-selected evidence, not independent provider attestation.</p>
  </section>;
}
