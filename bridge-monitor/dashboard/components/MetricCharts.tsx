import type { MetricSet, MetricValue } from '@/lib/contracts.generated';
import { metricText } from '@/lib/selection';

const SIZE = 144;
const STROKE = 14;
const R = (SIZE - STROKE) / 2;
const C = SIZE / 2;

type Tone = 'teal' | 'amber' | 'blue';
type Arc = { tone: Tone; fraction: number };
type Row = { label: string; tone: Tone | 'neutral'; text?: string; detail: string };

function finite(n: unknown): n is number {
  return typeof n === 'number' && Number.isFinite(n);
}

/** Clamped authoritative percent, or null when unknown (missing data, zero/absent denominator, bad percent). */
function rate(value?: MetricValue | null): number | null {
  if (!value || !finite(value.numerator) || !finite(value.denominator) || value.denominator <= 0 || !finite(value.percent)) return null;
  return Math.min(100, Math.max(0, value.percent));
}

function percentText(value?: MetricValue | null): string {
  return rate(value) === null ? 'N/A' : metricText(value ?? undefined);
}

function counts(value?: MetricValue | null): string {
  return value ? `${value.numerator} / ${value.denominator}` : 'Awaiting data';
}

/** Routing geometry uses ratios of authoritative counts so independently rounded percents cannot leave a gap. */
function routingArcs(items: Array<[Tone, MetricValue | undefined]>): { arcs: Arc[]; total: number } | null {
  const values = items.map(item => item[1]);
  if (values.some(v => rate(v) === null)) return null;
  const list = values as MetricValue[];
  const total = list[0].denominator;
  if (list.some(v => v.denominator !== total || v.numerator < 0)) return null;
  if (list.reduce((sum, value) => sum + value.numerator, 0) !== total) return null;
  const raw = items.map(([tone, v]) => ({ tone, fraction: ((v as MetricValue).numerator / total) * 100 }));
  return { arcs: raw.filter(a => a.fraction > 0), total };
}

function Donut({ label, arcs, main, caption }: { label: string; arcs: Arc[]; main: string; caption?: string }) {
  const segments = arcs.map((arc, i) => ({ ...arc, start: arcs.slice(0, i).reduce((s, a) => s + a.fraction, 0) }));
  return <svg className='donut' viewBox={`0 0 ${SIZE} ${SIZE}`} width={SIZE} height={SIZE} role='img' aria-label={label}>
    <circle className='donut-hole' cx={C} cy={C} r={R - STROKE / 2} />
    <g transform={`rotate(-90 ${C} ${C})`}>
      <circle className='donut-track' cx={C} cy={C} r={R} pathLength={100} />
      {segments.map(seg => <circle key={seg.tone} className={`donut-arc ${seg.tone}`} cx={C} cy={C} r={R} pathLength={100}
        strokeDasharray={`${seg.fraction} ${100 - seg.fraction}`} strokeDashoffset={-seg.start} />)}
    </g>
    <text className='donut-value' x={C} y={caption ? C - 1 : C + 8} textAnchor='middle' aria-hidden='true'>{main}</text>
    {caption && <text className='donut-caption' x={C} y={C + 17} textAnchor='middle' aria-hidden='true'>{caption}</text>}
  </svg>;
}

function Card({ title, unit, svgLabel, arcs, main, caption, rows }: {
  title: string; unit: string; svgLabel: string; arcs: Arc[]; main: string; caption?: string; rows: Row[];
}) {
  return <article className='chart-card' aria-label={`${title} chart`}>
    <h3>{title}</h3>
    <Donut label={svgLabel} arcs={arcs} main={main} caption={caption} />
    <ul className='chart-legend'>
      {rows.map(row => <li key={row.label}>
        <i className={`chart-swatch ${row.tone}`} aria-hidden='true' />
        <span className='chart-legend-text'><span>{row.label}</span>{row.text && <strong>{row.text}</strong>}<small>{row.detail}</small></span>
      </li>)}
    </ul>
    <p className='chart-unit'>Unit: {unit}</p>
  </article>;
}

function Single({ title, unit, rowLabel, value, tone }: { title: string; unit: string; rowLabel: string; value?: MetricValue; tone: Tone }) {
  const pct = rate(value);
  const arcs: Arc[] = pct !== null && pct > 0 ? [{ tone, fraction: pct }] : [];
  const rows: Row[] = [{ label: rowLabel, tone, text: percentText(value), detail: counts(value) }];
  if (pct !== null && value && value.numerator <= value.denominator) {
    rows.push({ label: 'Remainder', tone: 'neutral', detail: `${value.denominator - value.numerator} / ${value.denominator}` });
  }
  const svgLabel = pct === null ? `${title}: not available` : `${title}: ${percentText(value)}, ${counts(value)} ${unit}`;
  return <Card title={title} unit={unit} svgLabel={svgLabel} arcs={arcs} main={percentText(value)} rows={rows} />;
}

/** Noninteractive donut charts of service-reported percentages; each card keeps its own denominator. */
export default function MetricCharts({ report }: { report: MetricSet | null }) {
  const m = report?.metrics;
  const routing = routingArcs([['teal', m?.routed], ['amber', m?.not_routed], ['blue', m?.pending]]);
  const routingRows: Row[] = [
    { label: 'Routed', tone: 'teal', text: percentText(m?.routed), detail: counts(m?.routed) },
    { label: 'Not routed', tone: 'amber', text: percentText(m?.not_routed), detail: counts(m?.not_routed) },
    { label: 'Pending', tone: 'blue', text: percentText(m?.pending), detail: counts(m?.pending) },
  ];
  const routingLabel = routing
    ? `Routing: routed ${percentText(m?.routed)}, not routed ${percentText(m?.not_routed)}, pending ${percentText(m?.pending)} of ${routing.total} work items`
    : 'Routing: not available';
  return <div className='metric-charts' role='group' aria-label='Metric charts'>
    <Card title='Routing' unit='work items' svgLabel={routingLabel} arcs={routing?.arcs ?? []}
      main={routing ? String(routing.total) : 'N/A'} caption={routing ? 'work items' : undefined} rows={routingRows} />
    <Single title='Local contribution' unit='delivered results' rowLabel='Contribution rate' value={m?.contribution} tone='teal' />
    <Single title='Paid takeover' unit='routed work items' rowLabel='Takeover rate' value={m?.takeover} tone='amber' />
    <Single title='Disposition coverage' unit='delivered results' rowLabel='Coverage' value={report?.coverage?.disposition} tone='blue' />
  </div>;
}
