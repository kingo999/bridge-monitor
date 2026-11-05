'use client';
import { useEffect, useState } from 'react';
import type { CodexFlowModelSetting, FlowModelSetting } from './contracts.generated';

type Service = 'claude' | 'qwen' | 'gemma';
type Availability = Record<Service, boolean> & { modelSettings: Partial<Record<Service, FlowModelSetting[]>>;
  codexModels: CodexFlowModelSetting[] };
const unavailable: Availability = { claude: false, qwen: false, gemma: false, modelSettings: {}, codexModels: [] };

export function useServiceAvailability(paused: boolean) {
  const [services, setServices] = useState(unavailable);
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
          modelSettings: value.model_settings ?? {},
          codexModels: value.codex_models ?? [],
        });
      } catch {
        if (!controller.signal.aborted) setServices(unavailable);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, nextPoll);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [paused]);
  return services;
}
