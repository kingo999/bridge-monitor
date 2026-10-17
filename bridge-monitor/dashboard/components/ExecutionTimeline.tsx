import type { WorkDetail } from '@/lib/contracts.generated';

export default function ExecutionTimeline({ detail }: { detail: WorkDetail }) {
  return <>{detail.recovery_decisions?.flatMap(decision => decision.outcomes.map(outcome =>
    <p key={`${decision.call_id}-${decision.recorded_at}-${outcome.child_call_id}`} className="muted">
      Paid recovery for {outcome.child_call_id}: {outcome.state}
      {outcome.reason ? ` (${outcome.reason})` : ''}
    </p>))}<ol className="timeline">{detail.events.map(event => <li key={event.event_id}>
    <span className="timeline-mark" /><div><strong>{event.event_type}</strong>
      <time>{new Date(event.occurred_at).toLocaleString()}</time>
      <small>{event.call_id || event.worker_instance_id || ''}</small>
      {event.capture_completeness !== 'complete' && <em>Capture: {event.capture_completeness}</em>}
    </div></li>)}</ol>{detail.events_truncated &&
      <p className="muted">Timeline shows the first 500 events; further events are available in PostgreSQL.</p>}</>;
}
