import { DistributionChart } from './MetricCharts';

export default function ModelBreakdownChart({ models }: { models: Record<string, number> }) {
  const entries = Object.entries(models).filter(([, count]) => count > 0)
    .sort(([left, a], [right, b]) => b - a || left.localeCompare(right));
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  return <div className="cc-model-chart">
    <DistributionChart distribution={{
      title: 'Reported / requested models', total,
      basis: 'Workflows in the current cohort · Reported model when available, requested model otherwise; missing evidence stays Unknown.',
      slices: entries.map(([model, count]) => ({ key: model, label: model, count,
        percent: Math.round(count / total * 1000) / 10 })),
    }} />
  </div>;
}
