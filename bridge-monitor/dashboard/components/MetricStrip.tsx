import type { MetricSet } from '@/lib/contracts.generated';
import { metricLabel, metricText, type MetricKey } from '@/lib/selection';

const keys: MetricKey[] = ['routed', 'not_routed', 'pending', 'confirmed_acceptance',
  'contribution', 'takeover', 'local_resolution', 'disposition'];

export default function MetricStrip({ report, selected, onSelect, consumerStage }: {
  report: MetricSet | null; selected: MetricKey | null;
  onSelect: (key: MetricKey | null) => void; consumerStage: string;
}) {
  return <section className="metric-strip" aria-label="Delegation and outcome statistics">
    {keys.map(key => {
      const value = key === 'disposition' ? report?.coverage.disposition : report?.metrics[key];
      return <button className={`metric ${selected === key ? 'selected' : ''}`} key={key}
        type="button" onClick={() => onSelect(selected === key ? null : key)}
        aria-pressed={selected === key}>
        <span className="metric-label">{metricLabel(key, consumerStage)}</span>
        <strong>{metricText(value)}</strong>
        <small>{value ? `${value.numerator} / ${value.denominator}` : 'Awaiting data'}</small>
        {key === 'pending' && !!report?.coverage.resolved_without_delegation_record?.numerator &&
          <span className="metric-detail">{report.coverage.resolved_without_delegation_record.numerator} resolved without a delegation record</span>}
        {key === 'confirmed_acceptance' && report &&
          <span className="metric-detail">{consumerStage === 'gpt' &&
            report.coverage.disposition.numerator === 0
              ? 'No GPT acknowledgement captured; final use unknown'
              : 'Valid disposition records; not independently quality-reviewed'}</span>}
      </button>;
    })}
  </section>;
}
