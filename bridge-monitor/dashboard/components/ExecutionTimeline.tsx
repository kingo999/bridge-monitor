import { Fragment } from 'react';
import type { WorkDetail } from '@/lib/contracts.generated';

export default function ExecutionTimeline({ detail }: { detail: WorkDetail }) {
  return <>{detail.recovery_decisions?.flatMap(decision => decision.outcomes.map(outcome =>
    <p key={`${decision.call_id}-${decision.recorded_at}-${outcome.child_call_id}`} className="muted">
      Paid recovery for {outcome.child_call_id}: {outcome.state}
      {outcome.reason ? ` (${outcome.reason})` : ''}
    </p>))}<ol className="timeline">{detail.events.map((event, index) => <Fragment key={event.event_id}>
    {detail.events_truncated && index === detail.events_gap_after && <li className="timeline-gap">
      <div><strong>Middle events omitted from this view</strong>
        <small>Complete event history is retained in PostgreSQL.</small></div>
    </li>}
    <li><span className="timeline-mark" /><div><strong>{event.event_type}</strong>
      <time>{new Date(event.occurred_at).toLocaleString()}</time>
      <small>{event.call_id || event.worker_instance_id || ''}</small>
      {event.capture_completeness !== 'complete' && <em>Capture: {event.capture_completeness}</em>}
    </div></li></Fragment>)}</ol>{detail.events_truncated && detail.events_gap_after != null &&
      <p className="muted">Timeline shows the first {detail.events_gap_after} and latest
        {' '}{detail.events.length - detail.events_gap_after} events.</p>}</>;
}
