import { useEffect, useState } from 'react';
import { auditRequest } from './audit';

type HandoffPage = {
  available: boolean; total: number | null; next: string | null;
  rows: { id: string; time: string; workflow_id: string; iteration: number; action: string }[];
  chat_selection?: { model?: string; effort?: string; time: string };
};

export function useHandoffPage(query: string, active = true) {
  const [page, setPage] = useState<HandoffPage | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setPage(null);
    setError('');
    async function refresh() {
      try {
        const result = await auditRequest<HandoffPage>('design-handoffs?' + query, undefined,
          AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]));
        if (!controller.signal.aborted) { setPage(result); setError(''); }
      } catch {
        if (!controller.signal.aborted) { setPage(null); setError('Design Mode handoff history is unavailable.'); }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 15000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active, query]);
  return { page, error };
}
