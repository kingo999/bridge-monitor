'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { UsageNoticeReport } from '@/lib/contracts.generated';

export default function UsageNotices({ refreshKey }: { refreshKey: string }) {
  const [report, setReport] = useState<UsageNoticeReport | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => api.usageNotices(controller.signal).then(value => {
      if (!controller.signal.aborted) { setReport(value); setError(''); }
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Unavailable');
    });
    void refresh();
    const timer = setInterval(() => void refresh(), 30000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [refreshKey]);
  return <section className="usage-notices panel" aria-label="Usage notices">
    <div className="usage-notice-heading"><h2>Usage notices</h2>
      <span>Notice only · No global daily hard cap</span></div>
    {error && <p className="notice-data-error" role="status">Usage data unavailable; any last displayed values may be stale. {error}</p>}
    {report?.notices.map(notice => <article className="usage-notice-warning" key={notice.id} role="status">
      <strong>⚠ {notice.title}</strong><p>{notice.message}</p>
      <small>{notice.basis}{notice.period_end ? ' · Period ends ' + new Date(notice.period_end).toLocaleString() : ''}</small>
    </article>)}
    <div className="usage-notice-facts">
      <p>Daily usage: <strong>{report?.daily.known_subtotal_usd != null ? '$' + Number(report.daily.known_subtotal_usd).toFixed(2) : 'Unknown'}</strong>
        {' '}API-equivalent · notice above $10 · Europe/Prague calendar day</p>
      <p>Subscription allowance: <strong>{report?.subscription.status === 'current' ?
        report.subscription.windows.filter(window => window.status === 'current')
          .map(window => window.provider + ' ' + window.consumed_percent + '% (' + window.window_id + ')').join(' · ') : 'Unknown'}</strong>
        {' '}· notice at 80%</p>
    </div>
    <p className="usage-scope">{report?.daily.scope || 'Observed settled Bridge usage only; provider quota is never inferred from dollars.'}
      {report?.daily.unknown_usage_calls ? ' · ' + report.daily.unknown_usage_calls + ' call(s) without settled cost evidence' : ''}
      {report ? ' · Observed ' + new Date(report.as_of).toLocaleString() : ' · Loading current data…'}</p>
  </section>;
}
