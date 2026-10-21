'use client';

import { useEffect, useState } from 'react';
import { api, type ClassifierActivity } from './api';

export function useClassifierActivity(paused: boolean) {
  const [activity, setActivity] = useState<ClassifierActivity | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (paused) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await api.classifierActivity(AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]));
        if (!controller.signal.aborted) { setActivity(result); setFailed(false); }
      } catch {
        if (!controller.signal.aborted) { setActivity(null); setFailed(true); }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 1000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [paused]);
  return { activity, failed };
}
