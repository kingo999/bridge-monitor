'use client';
import { useEffect, useState } from 'react';

export function useServiceAvailability(paused: boolean) {
  const [services, setServices] = useState<Record<'claude' | 'qwen' | 'gemma', boolean>>({
    claude: false, qwen: false, gemma: false,
  });
  useEffect(() => {
    if (paused) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      let nextPoll = 10000;
      try {
        const response = await fetch('/api/v1/services', { credentials: 'same-origin', cache: 'no-store',
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) });
        if (!response.ok) throw new Error('Service status unavailable');
        const value = await response.json();
        if (value.refreshing) nextPoll = 500;
        if (!controller.signal.aborted) setServices({
          claude: value.services?.claude === true, qwen: value.services?.qwen === true, gemma: value.services?.gemma === true,
        });
      } catch {
        if (!controller.signal.aborted) setServices({ claude: false, qwen: false, gemma: false });
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, nextPoll);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [paused]);
  return services;
}
