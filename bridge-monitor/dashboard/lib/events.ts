import type { Origin, Publication, Snapshot } from './contracts.generated';

export type StreamState = 'connecting' | 'live' | 'offline' | 'replaying' | 'gap';

export function connectEvents(
  cursor: number, origin: Origin,
  onPublication: (publication: Publication) => void,
  onReset: (snapshot: Snapshot) => void,
  onState: (state: StreamState) => void,
  onSessionExpired: () => void,
): EventSource {
  const source = new EventSource(`/api/v1/stream?cursor=${cursor}&origin=${origin}`, { withCredentials: true });
  source.onopen = () => onState('live');
  source.onerror = () => onState('offline');
  source.addEventListener('publication', (event) => {
    try { onPublication(JSON.parse((event as MessageEvent).data) as Publication); }
    catch { onState('gap'); }
  });
  source.addEventListener('reset', (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as { snapshot: Snapshot };
    onState('gap');
    onReset(payload.snapshot);
  });
  source.addEventListener('session-expired', onSessionExpired);
  return source;
}
