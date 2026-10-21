import type { MetricValue, WorkItem } from './contracts.generated';

export type MetricKey = 'routed' | 'not_routed' | 'pending' | 'confirmed_acceptance' |
  'contribution' | 'takeover' | 'local_resolution' | 'disposition';

export function metricLabel(key: MetricKey, consumerStage = 'claude'): string {
  return ({ routed: 'Routed to Skynet', not_routed: 'Not routed', pending: 'Routing pending',
    confirmed_acceptance: `${consumerStage === 'gpt' ? 'GPT' : 'Claude'}-reported acceptance`,
    contribution: 'Local contribution',
    takeover: 'Paid takeover', local_resolution: 'Locally resolved',
    disposition: 'Disposition coverage' })[key];
}

export function metricText(value?: MetricValue): string {
  return value?.percent == null ? 'N/A' : `${value.percent}%`;
}

export function matchesMetric(item: WorkItem, key: MetricKey | null): boolean {
  if (!key) return true;
  if (item.execution_path === 'direct_codex') return false;
  if (key === 'routed') return item.routed;
  if (key === 'not_routed') return !item.routed && item.paid_route;
  if (key === 'pending') return !item.routed && !item.paid_route;
  if (key === 'takeover') return item.resolution === 'paid_replaced';
  if (key === 'local_resolution') return item.resolution === 'local_retained';
  if (key === 'confirmed_acceptance') return item.disposition_judgment === 'accepted';
  if (key === 'contribution') return item.disposition_judgment === 'accepted' || item.disposition_judgment === 'revised';
  if (key === 'disposition') return !!item.disposition_judgment;
  return true;
}
