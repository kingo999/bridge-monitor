import type { MetricSet } from '@/lib/contracts.generated';
import { metricLabel, metricText, type MetricKey } from '@/lib/selection';

const keys: MetricKey[] = ['routed', 'not_routed', 'pending', 'confirmed_acceptance',
  'contribution', 'takeover', 'local_resolution', 'disposition'];

export default function MetricStrip({ report, selected, onSelect }: {
  report: MetricSet | null; selected: MetricKey | null; onSelect: (key: MetricKey | null) => void;
}) {
  return <section className="metric-strip" aria-label="Delegation and outcome statistics">
    {keys.map(key => {
      const value = key === 'disposition' ? report?.coverage.disposition : report?.metrics[key];
      return <button className={`metric ${selected === key ? 'selected' : ''}`} key={key}
        type="button" onClick={() => onSelect(selected === key ? null : key)}
        aria-pressed={selected === key}>
        <span className="metric-label">{metricLabel(key)}</span>
        <strong>{metricText(value)}</strong>
        <small>{value ? `${value.numerator} / ${value.denominator}` : 'Awaiting data'}</small>
      </button>;
    })}
  </section>;
}
