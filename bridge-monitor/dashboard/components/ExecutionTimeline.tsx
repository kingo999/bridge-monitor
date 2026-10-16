import type { WorkDetail } from '@/lib/contracts.generated';

export default function ExecutionTimeline({ detail }: { detail: WorkDetail }) {
  return <ol className="timeline">{detail.events.map(event => <li key={event.event_id}>
    <span className="timeline-mark" /><div><strong>{event.event_type}</strong>
      <time>{new Date(event.occurred_at).toLocaleString()}</time>
      <small>{event.call_id || event.worker_instance_id || ''}</small>
      {event.capture_completeness !== 'complete' && <em>Capture: {event.capture_completeness}</em>}
    </div></li>)}</ol>;
}
