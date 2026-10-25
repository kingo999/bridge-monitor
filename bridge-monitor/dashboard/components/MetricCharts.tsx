import type { Distribution, DistributionSlice, MetricSet } from '@/lib/contracts.generated';

const SIZE = 164;
const STROKE = 17;
const R = (SIZE - STROKE) / 2;
const C = SIZE / 2;
const KEYS = ['node', 'model', 'handling', 'tier'] as const;

function color(key: string): string {
  if (key.toLowerCase() === 'unknown') return 'unknown';
  if (key === 'in_progress') return 'in-progress';
  if (key === 'mixed') return 'mixed';
  const colors: Record<string, string> = {
    codex: 'series-1', claude: 'series-2', skynet: 'series-3', gemma: 'series-4',
    sol: 'series-1', luna: 'series-2', astra: 'series-3', opus: 'series-4',
    sonnet: 'series-5', qwen: 'series-6', haiku: 'series-7', other: 'series-8',
    qwen_full: 'series-3', qwen_partial: 'series-6', claude_only: 'series-2',
    codex_only: 'series-1', paid: 'series-2', local: 'series-3',
    'gpt-6.1-sol': 'series-1', 'claude-sonnet-5-5': 'series-2',
    'gpt-6-astra': 'series-3', 'gpt-6-luna': 'series-4', 'gpt-6-sol': 'series-6',
    'claude-haiku-5-5': 'series-7', 'claude-opus-5-5': 'series-8',
  };
  return colors[key] || 'series-8';
}

function Donut({ distribution, slices }: { distribution: Distribution; slices: DistributionSlice[] }) {
  const { total, title } = distribution;
  let start = 0;
  const arcs = slices.map(slice => {
    const fraction = total > 0 ? 100 * slice.count / total : 0;
    const arc = { ...slice, start, fraction };
    start += fraction;
    return arc;
  });
  const summary = total > 0
    ? `${title}: ${slices.map(slice => `${slice.label} ${slice.count} (${slice.percent ?? 0}%)`).join(', ')}; total ${total} work items`
    : `${title}: no work items in this cohort`;
  return <svg className="donut" viewBox={`0 0 ${SIZE} ${SIZE}`} width={SIZE} height={SIZE}
    role="img" aria-label={summary}>
    <circle className="donut-hole" cx={C} cy={C} r={R - STROKE / 2} />
    <g transform={`rotate(-90 ${C} ${C})`}>
      <circle className="donut-track" cx={C} cy={C} r={R} pathLength={100} />
      {arcs.map(arc => <circle key={arc.key} className={`donut-arc ${color(arc.key)}`}
        cx={C} cy={C} r={R} pathLength={100}
        strokeDasharray={`${arc.fraction} ${100 - arc.fraction}`} strokeDashoffset={-arc.start} />)}
    </g>
    <text className="donut-value" x={C} y={C - 1} textAnchor="middle" aria-hidden="true">{total || 'N/A'}</text>
    <text className="donut-caption" x={C} y={C + 18} textAnchor="middle" aria-hidden="true">work items</text>
  </svg>;
}

function ChartTag({ slice, total }: { slice: DistributionSlice; total: number }) {
  return <li className="chart-tag" title={`${slice.label}: ${slice.count} of ${total} (${slice.percent ?? 0}%)`}>
    <i className={`chart-swatch ${color(slice.key)}`} aria-hidden="true" />
    <span className="chart-tag-label">{slice.label}</span>
    <span className="chart-tag-count">{slice.count}</span>
    <strong>{slice.percent ?? 0}%</strong>
  </li>;
}

export function DistributionChart({ distribution }: { distribution: Distribution }) {
  const visibleSlices = distribution.slices.filter(slice => slice.count > 0);
  return <article className="chart-card" aria-label={`${distribution.title} chart`}>
    <h3>{distribution.title}</h3>
    <div className="chart-content">
      <Donut distribution={distribution} slices={visibleSlices} />
      {visibleSlices.length > 0
        ? <ul className="chart-tags" aria-label={`${distribution.title} categories`}>
          {visibleSlices.map(slice => <ChartTag key={slice.key} slice={slice} total={distribution.total} />)}
        </ul>
        : <p className="chart-empty">No nonzero categories</p>}
    </div>
    <p className="chart-unit">{distribution.basis}</p>
  </article>;
}

export default function MetricCharts({ report }: { report: MetricSet | null }) {
  const distributions = report?.distributions;
  if (!distributions) return <p className="muted">Work distribution data is not available yet.</p>;
  return <div className="metric-charts" role="group" aria-label="Work distribution charts">
    {KEYS.map(key => <DistributionChart key={key} distribution={distributions[key]} />)}
  </div>;
}
