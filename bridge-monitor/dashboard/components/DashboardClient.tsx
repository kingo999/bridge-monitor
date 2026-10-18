'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, AuthenticationRequired, type CohortFilters } from '@/lib/api';
import { connectEvents, type StreamState } from '@/lib/events';
import type { MetricSet, Snapshot, WorkItem } from '@/lib/contracts.generated';
import { matchesMetric, metricLabel, type MetricKey } from '@/lib/selection';
import CohortControls from './CohortControls';
import ExchangeInspector from './ExchangeInspector';
import FlowGraph from './FlowGraph';
import MetricStrip from './MetricStrip';
import WorkList from './WorkList';

const initialFilters: CohortFilters = {
  origin: 'observed', consumerStage: 'claude', createdFrom: '', createdUntil: '', completedOnly: false,
  guidanceGroup: '', failureCategory: '', repairEffort: '', exampleReuseStatus: '',
};

export default function DashboardClient() {
  const [auth, setAuth] = useState<'checking' | 'login' | 'ready'>('checking');
  const [operatorToken, setOperatorToken] = useState('');
  const [loginError, setLoginError] = useState('');
  const [filters, setFilters] = useState<CohortFilters>(initialFilters);
  const [mode, setMode] = useState<'live' | 'history'>('live');
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [metrics, setMetrics] = useState<MetricSet | null>(null);
  const [items, setItems] = useState<WorkItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedMetric, setSelectedMetric] = useState<MetricKey | null>(null);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(true);
  const [paused, setPaused] = useState(false);
  const [pendingDisplay, setPendingDisplay] = useState(false);
  const [streamState, setStreamState] = useState<StreamState>('connecting');
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [more, setMore] = useState(false);
  const [error, setError] = useState('');
  const cursorRef = useRef(0);
  const seenRef = useRef(new Set<string>());
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pausedRef = useRef(false);
  const selectedRef = useRef<string | null>(null);
  const pausedDetailRef = useRef<string | null>(null);
  const expandedListTail = useRef<string | null>(null);
  const workItemMaxLimit = useRef(100);
  const filtersRef = useRef(filters);
  const latestOffset = useRef(0);
  const reloadSequence = useRef(0);
  const toggleExpanded = useCallback(() => setExpanded(value => !value), []);
  const focusEdge = useCallback((route: 'routed' | 'not_routed') => setSelectedMetric(route), []);
  const hasSnapshot = snapshot !== null;

  useEffect(() => { pausedRef.current = paused; }, [paused]);
  useEffect(() => { selectedRef.current = selectedId; }, [selectedId]);
  useEffect(() => { filtersRef.current = filters; }, [filters]);

  useEffect(() => {
    api.session().then(value => {
      workItemMaxLimit.current = value.work_item_max_limit || 100;
      setAuth(value.authenticated ? 'ready' : 'login');
    })
      .catch(() => setAuth('login'));
  }, []);

  const reload = useCallback(async (cohort: CohortFilters, includeSnapshot = true) => {
    const sequence = ++reloadSequence.current;
    setLoading(true);
    try {
      const firstLimit = expandedListTail.current ?
        Math.min(workItemMaxLimit.current, Math.max(100, latestOffset.current + 100)) : 100;
      const nextSnapshot = includeSnapshot ? await api.snapshot('all') : null;
      const [nextMetrics, firstPage] = await Promise.all([
        api.metrics(cohort), api.workItems(cohort, 0, firstLimit),
      ]);
      const nextItems = [...firstPage];
      let page = firstPage;
      let pageLimit = firstLimit;
      while (expandedListTail.current && page.length === pageLimit &&
             !nextItems.some(item => item.work_item_id === expandedListTail.current)) {
        pageLimit = 100;
        page = await api.workItems(cohort, nextItems.length, pageLimit);
        nextItems.push(...page);
      }
      if (sequence !== reloadSequence.current) return;
      if (nextSnapshot) { setSnapshot(nextSnapshot); cursorRef.current = Math.max(cursorRef.current, nextSnapshot.cursor.position); }
      setMetrics(nextMetrics);
      setItems(nextItems);
      setMore(page.length === pageLimit);
      latestOffset.current = nextItems.length;
      setError('');
    } catch (cause) {
      if (sequence !== reloadSequence.current) return;
      if (cause instanceof AuthenticationRequired) setAuth('login');
      else setError(cause instanceof Error ? cause.message : 'Could not load dashboard data');
    } finally { if (sequence === reloadSequence.current) setLoading(false); }
  }, []);

  useEffect(() => { if (auth === 'ready') void reload(filters); }, [auth, filters, reload]);

  useEffect(() => {
    if (auth !== 'ready' || !hasSnapshot) return;
    const source = connectEvents(cursorRef.current, 'all', publication => {
      if (publication.position <= cursorRef.current && seenRef.current.has(publication.event_id)) return;
      if (seenRef.current.has(publication.event_id)) return;
      seenRef.current.add(publication.event_id);
      if (seenRef.current.size > 2000) seenRef.current.clear();
      cursorRef.current = Math.max(cursorRef.current, publication.position);
      if (selectedRef.current === publication.work_item_id) {
        if (pausedRef.current) pausedDetailRef.current = publication.work_item_id;
        else setDetailRefresh(value => value + 1);
      }
      if (pausedRef.current) { setPendingDisplay(true); return; }
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => { void reload(filtersRef.current); }, 200);
    }, nextSnapshot => {
      cursorRef.current = nextSnapshot.cursor.position;
      seenRef.current.clear();
      if (pausedRef.current) {
        setPendingDisplay(true);
        pausedDetailRef.current = selectedRef.current;
      } else {
        setSnapshot(nextSnapshot);
        if (selectedRef.current) setDetailRefresh(value => value + 1);
        void reload(filtersRef.current, false);
      }
    }, setStreamState, () => setAuth('login'));
    return () => { source.close(); if (refreshTimer.current) clearTimeout(refreshTimer.current); };
  }, [auth, hasSnapshot, reload]);

  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLoginError('');
    try {
      const session = await api.login(operatorToken);
      workItemMaxLimit.current = session.work_item_max_limit || 100;
      setOperatorToken(''); setAuth('ready');
    }
    catch { setLoginError('Invalid operator credential or service unavailable'); }
  }

  function togglePause() {
    if (paused) {
      pausedRef.current = false;
      setPaused(false);
      setPendingDisplay(false);
      if (pausedDetailRef.current === selectedRef.current) setDetailRefresh(value => value + 1);
      pausedDetailRef.current = null;
      void reload(filters);
    } else {
      pausedRef.current = true;
      setPaused(true);
    }
  }

  async function loadMore() {
    const sequence = reloadSequence.current;
    const page = await api.workItems(filters, latestOffset.current);
    if (sequence !== reloadSequence.current) return;
    setItems(current => [...current, ...page.filter(next => !current.some(old => old.work_item_id === next.work_item_id))]);
    latestOffset.current += page.length;
    if (page.length) expandedListTail.current = page[page.length - 1].work_item_id;
    setMore(page.length === 100);
  }

  function changeFilters(next: CohortFilters) {
    reloadSequence.current += 1;
    expandedListTail.current = null;
    latestOffset.current = 0;
    setFilters(next);
  }

  const visibleItems = items.filter(item => matchesMetric(item, selectedMetric) &&
    `${item.work_item_id} ${item.category || ''} ${item.reported_model || ''}`.toLowerCase().includes(search.toLowerCase()));
  const liveItems = snapshot?.work_items || [];

  if (auth === 'checking') return <main className="loading-screen"><div className="brand-mark">↗</div><h1>Bridge Monitor</h1><p>Checking operator session…</p></main>;
  if (auth === 'login') return <main className="login-screen"><form onSubmit={login} className="login-card">
    <div className="brand-mark">↗</div><h1>Bridge Monitor</h1><p>Operator access is required to inspect exchanges.</p>
    <label>Operator credential<input type="password" autoComplete="current-password" value={operatorToken}
      onChange={event => setOperatorToken(event.target.value)} required autoFocus /></label>
    {loginError && <p className="error" role="alert">{loginError}</p>}
    <button type="submit">Open dashboard</button></form></main>;

  return <main className="dashboard">
    <header className="app-header"><div className="brand"><span className="brand-mark">↗</span><h1>Bridge Monitor</h1>
      <span className={`connection ${streamState}`} role="status"><i className="dot" /> {streamState === 'live' ? 'Connected' : streamState}</span></div>
      <div className="header-actions"><CohortControls filters={filters} onChange={changeFilters} mode={mode} onMode={setMode}
        paused={paused} onPause={togglePause} />
        <button className="logout" type="button" onClick={async () => { await api.logout(); setAuth('login'); }}>Sign out</button></div></header>
    <div className="status-line"><span>{mode === 'history' ? 'Historical cohort' : 'Live monitor'} · {filters.origin} · {metrics?.metrics.routed.denominator ?? 0} work items</span>
      {snapshot && <span>Snapshot {new Date(snapshot.as_of).toLocaleString()}</span>}</div>
    {(error || pendingDisplay || streamState === 'offline' || streamState === 'gap' || snapshot?.status.capture_state === 'degraded' || !!snapshot?.status.known_gaps) &&
      <div className="alert-bar" role="status">{error && <span>Data error: {error}. </span>}
        {pendingDisplay && <span>Display paused · new events received. </span>}
        {streamState === 'offline' && <span>Stream offline · retrying. </span>}
        {streamState === 'gap' && <span>Replay gap · snapshot refreshed. </span>}
        {snapshot?.status.capture_state === 'degraded' && <span>Capture degraded. </span>}
        {!!snapshot?.status.known_gaps && <span>{snapshot.status.known_gaps} known capture gap(s). </span>}</div>}
    <MetricStrip report={metrics} selected={selectedMetric} onSelect={setSelectedMetric}
      consumerStage={filters.consumerStage} />
    {selectedMetric && <p className="filter-note">Loaded work list filtered by {metricLabel(selectedMetric, filters.consumerStage)}. Numerator/denominator and coverage come from the service; result-unit metrics may map several results to one work item.</p>}
    <div className="dashboard-grid"><div className="left-column">
      <FlowGraph status={snapshot?.status || null} capacity={snapshot?.capacity || null} items={liveItems} executions={snapshot?.worker_executions || []} selectedId={selectedId}
        expanded={expanded} onToggle={toggleExpanded} onSelect={setSelectedId}
        onEdge={focusEdge} />
      <WorkList items={visibleItems} selectedId={selectedId} onSelect={setSelectedId}
        search={search} onSearch={setSearch} loading={loading} more={more} onMore={() => void loadMore()} />
    </div><ExchangeInspector id={selectedId} refreshKey={detailRefresh} paused={paused} /></div>
    <footer className="app-footer"><span>Current topology remains live while historical filters control metrics and work list.</span>
      <span>Definition {metrics?.definition_version || '—'} · As of {metrics ? new Date(metrics.as_of).toLocaleString() : '—'}</span></footer>
  </main>;
}
