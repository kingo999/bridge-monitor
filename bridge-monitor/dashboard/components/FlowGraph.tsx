'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Position, ReactFlow, type Edge, type Node, type ReactFlowInstance } from '@xyflow/react';
import type { DashboardStatus, PoolCapacity, WorkerExecution, WorkItem } from '@/lib/contracts.generated';
import { MarkerType } from '@xyflow/react';
import { useHandoffPage } from '@/lib/useHandoffPage';
import { formatFlowModel, HANDOFF_ACTIVITY_MS, handoffActivityRemaining } from '@/lib/flowActivity';
import WorkerGroup from './WorkerGroup';
import { useServiceAvailability } from '@/lib/useServiceAvailability';
import ClassifierActivity from './ClassifierActivity';
import { useClassifierActivity } from '@/lib/useClassifierActivity';
import NodeActivityStatus, { nodeActivityClass, nodeActivityState } from './NodeActivityStatus';

export default function FlowGraph({ status, capacity, items, executions, selectedId, expanded, onToggle, onSelect, onEdge, onHandoffs, paused = false }: {
  status: DashboardStatus | null; capacity: Record<'claude' | 'qwen', PoolCapacity> | null;
  items: WorkItem[]; executions: WorkerExecution[]; selectedId: string | null;
  expanded: boolean; onToggle: () => void; onSelect: (id: string) => void;
  onEdge: (route: 'routed' | 'not_routed') => void;
  onHandoffs: () => void;
  paused?: boolean;
}) {
  const { page: handoffs, error: handoffError } = useHandoffPage('limit=1', !paused);
  const total = handoffs?.total;
  const latestHandoffTime = handoffs?.rows[0]?.time;
  const chatSelection = handoffs?.available && !handoffError ? handoffs.chat_selection : undefined;
  const handoffAvailable = handoffs?.available === true && !handoffError && !paused;
  const [handoffNow, setHandoffNow] = useState(() => Date.now());
  const handoffActive = handoffActivityRemaining(latestHandoffTime, handoffNow, handoffAvailable) > 0;
  useEffect(() => {
    const now = Date.now();
    setHandoffNow(now);
    const remaining = handoffActivityRemaining(latestHandoffTime, now, handoffAvailable);
    if (!remaining || !latestHandoffTime) return;
    const expiresAt = Date.parse(latestHandoffTime) + HANDOFF_ACTIVITY_MS;
    const timer = setTimeout(() => setHandoffNow(Math.max(Date.now(), expiresAt)), remaining);
    return () => clearTimeout(timer);
  }, [latestHandoffTime, handoffAvailable]);
  const { modelSettings, codexModels, ...services } = useServiceAvailability(paused);
  const qwenModel = modelSettings.qwen?.[0];
  const gemmaModel = modelSettings.gemma?.[0];
  const canvasRef = useRef<HTMLDivElement>(null);
  const [instance, setInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const active = capacity?.claude.active ?? status?.active ?? 0;
  const claudeQueued = capacity?.claude.queued ?? status?.queued ?? 0;
  const qwenActive = capacity?.qwen.active ?? 0;
  const delegateActive = !paused && services.claude && active > 0;
  const localActive = !paused && services.claude && services.qwen && qwenActive > 0;
  const originState = nodeActivityState({ active: 0, queued: active + claudeQueued,
    available: !!capacity || !!status, paused });
  const qwenState = nodeActivityState({ active: qwenActive, queued: capacity?.qwen.queued ?? 0,
    uncertain: capacity?.qwen.held_uncertain ?? 0, available: !!capacity, paused });
  const { activity, failed } = useClassifierActivity(paused);
  const gemmaActive = !paused && !!activity?.available && ((activity.classifying ?? 0) > 0 || (activity.queued ?? 0) > 0);
  const completedAge = activity?.latest?.completed_at ? Date.parse(activity.checked_at) - Date.parse(activity.latest.completed_at) : Infinity;
  const recentResult = !paused && !!activity?.available && !gemmaActive && activity.latest?.state === 'completed' && completedAge >= 0 && completedAge < 8000;
  useEffect(() => {
    if (!instance || !canvasRef.current) return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { void instance.fitView({ padding: 0.12, duration: 0 }); });
    });
    observer.observe(canvasRef.current);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [instance]);
  const allNodes = useMemo<Node[]>(() => [
    { id: 'chatgpt', position: { x: -250, y: 85 }, type: 'input', sourcePosition: Position.Right,
      draggable: false, style: { width: 160 }, data: { label: <div className="flow-node origin-node">
        <div className="node-orb gpt" aria-hidden="true">
          {/* OpenAI brand icon: Tabler Icons, MIT (github.com/tabler/tabler-icons). */}
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M11.217 19.384a3.501 3.501 0 0 0 6.783 -1.217v-5.167l-6 -3.35" />
            <path d="M5.214 15.014a3.501 3.501 0 0 0 4.446 5.266l4.34 -2.534v-6.946" />
            <path d="M6 7.63c-1.391 -.236 -2.787 .395 -3.534 1.689a3.474 3.474 0 0 0 1.271 4.745l4.263 2.514l6 -3.348" />
            <path d="M12.783 4.616a3.501 3.501 0 0 0 -6.783 1.217v5.067l6 3.45" />
            <path d="M18.786 8.986a3.501 3.501 0 0 0 -4.446 -5.266l-4.34 2.534v6.946" />
            <path d="M18 16.302c1.391 .236 2.787 -.395 3.534 -1.689a3.474 3.474 0 0 0 -1.271 -4.745l-4.308 -2.514l-5.955 3.42" />
          </svg>
        </div>
        <strong>ChatGPT</strong><small>Design Mode</small>
        <small className="flow-model-details" title={chatSelection && new Date(chatSelection.time).toLocaleString()}>
          {formatFlowModel(chatSelection?.model, chatSelection?.effort, 'Last handoff')}
        </small>
        <button className="nodrag chatgpt-handoffs" type="button" onClick={onHandoffs} aria-label="Open Design Mode handoff audit">
          {total != null ? `${total.toLocaleString()} handoff${total === 1 ? '' : 's'}` :
            handoffError || handoffs ? 'Count unavailable' : 'View handoffs'}
        </button></div> } },
    { id: 'gpt', position: { x: 0, y: 85 }, sourcePosition: Position.Right, targetPosition: Position.Left, data: { label: <div className={`flow-node origin-node${nodeActivityClass(originState)}`}>
      <div className="node-orb gpt" aria-hidden="true">
        {/* Codex icon: LobeHub Icons, MIT (github.com/lobehub/lobe-icons). */}
        <svg width="44" height="44" viewBox="0 0 24 24" fill="currentColor" fillRule="evenodd">
          <path clipRule="evenodd" d="M8.086.457a6.105 6.105 0 013.046-.415c1.333.153 2.521.72 3.564 1.7a.117.117 0 00.107.029c1.408-.346 2.762-.224 4.061.366l.063.03.154.076c1.357.703 2.33 1.77 2.918 3.198.278.679.418 1.388.421 2.126a5.655 5.655 0 01-.18 1.631.167.167 0 00.04.155 5.982 5.982 0 011.578 2.891c.385 1.901-.01 3.615-1.183 5.14l-.182.22a6.063 6.063 0 01-2.934 1.851.162.162 0 00-.108.102c-.255.736-.511 1.364-.987 1.992-1.199 1.582-2.962 2.462-4.948 2.451-1.583-.008-2.986-.587-4.21-1.736a.145.145 0 00-.14-.032c-.518.167-1.04.191-1.604.185a5.924 5.924 0 01-2.595-.622 6.058 6.058 0 01-2.146-1.781c-.203-.269-.404-.522-.551-.821a7.74 7.74 0 01-.495-1.283 6.11 6.11 0 01-.017-3.064.166.166 0 00.008-.074.115.115 0 00-.037-.064 5.958 5.958 0 01-1.38-2.202 5.196 5.196 0 01-.333-1.589 6.915 6.915 0 01.188-2.132c.45-1.484 1.309-2.648 2.577-3.493.282-.188.55-.334.802-.438.286-.12.573-.22.861-.304a.129.129 0 00.087-.087A6.016 6.016 0 015.635 2.31C6.315 1.464 7.132.846 8.086.457zm-.804 7.85a.848.848 0 00-1.473.842l1.694 2.965-1.688 2.848a.849.849 0 001.46.864l1.94-3.272a.849.849 0 00.007-.854l-1.94-3.393zm5.446 6.24a.849.849 0 000 1.695h4.848a.849.849 0 000-1.696h-4.848z" />
        </svg>
      </div><strong>GPT / Codex</strong><small>Main conversation</small>
      {codexModels?.length ? codexModels.map(({ model, effort, effort_source }) => {
        const label = formatFlowModel(model, effort) + (effort_source === 'last_configured' ? ' · Last configured' : '');
        return <small className="flow-model-details" key={label}>{label}</small>;
      })
        : <small className="flow-model-details">{formatFlowModel()}</small>}
      <NodeActivityStatus state={originState} label={originState === 'waiting' ? 'Waiting for bridge workers' : undefined} /></div> }, type: 'default', draggable: false, style: { width: 160 } },
    { id: 'claude', position: { x: 215, y: 0 }, data: { label: <WorkerGroup active={active} queued={claudeQueued}
      held={capacity?.claude.held_uncertain ?? status?.held_uncertain ?? 0} blocked={status?.blocked ?? 0} executions={executions}
      expanded={expanded} onToggle={onToggle} onSelect={onSelect} paused={paused} available={services.claude && (!!capacity || !!status)} /> }, type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 250 } },
    { id: 'qwen', position: { x: 515, y: 85 }, data: { label: <div className={`flow-node skynet-node${nodeActivityClass(qwenState)}`}><div className="node-orb qwen">✦</div><strong>Skynet / Qwen</strong><small>RTX 4080 Super · local inference</small>
      <small className="flow-model-details">{formatFlowModel(qwenModel?.model, qwenModel?.effort, 'Configured')}</small>
      <NodeActivityStatus state={qwenState} queued={capacity?.qwen.queued ?? 0} />
      {capacity && <small>{capacity.qwen.active} active · {capacity.qwen.queued} queued · {capacity.qwen.held_uncertain} held uncertain</small>}</div> }, type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 170 } },
    { id: 'gemma', position: { x: 215, y: 265 }, data: { label: <ClassifierActivity activity={activity} failed={failed} paused={paused} modelSetting={gemmaModel} /> },
      type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 250 } },
  ], [total, handoffError, handoffs, onHandoffs, active, claudeQueued, qwenActive, originState, qwenState, status, capacity, executions, expanded, onToggle, onSelect, activity, failed, paused, services.claude, qwenModel, gemmaModel, chatSelection, codexModels]);
  const allEdges = useMemo<Edge[]>(() => [
    { id: 'chatgpt-gpt', source: 'chatgpt', target: 'gpt', label: 'Handoffs',
      animated: handoffActive, ariaLabel: `Handoffs · ${handoffActive ? 'recent activity' : 'idle'}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: handoffActive ? '#05d7c0' : '#64748b' },
      style: { stroke: handoffActive ? '#05d7c0' : '#64748b', strokeWidth: 2 },
      labelStyle: { fill: handoffActive ? '#91d7d4' : '#94a3b8' } },
    { id: 'gpt-claude', source: 'gpt', target: 'claude', label: 'Delegate', animated: delegateActive,
      ariaLabel: `Delegate · ${delegateActive ? 'active' : 'idle'}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: delegateActive ? '#05d7c0' : '#64748b' },
      style: { stroke: delegateActive ? '#05d7c0' : '#64748b', strokeWidth: selectedId ? 3 : 2 },
      labelStyle: { fill: delegateActive ? '#91d7d4' : '#94a3b8' } },
    { id: 'claude-qwen', source: 'claude', target: 'qwen', label: 'Request / return', animated: localActive,
      style: { stroke: localActive ? '#9a80f5' : '#64748b', strokeWidth: selectedId && items.find(x => x.work_item_id === selectedId)?.routed ? 3 : 2 },
      labelStyle: { fill: localActive ? '#b9aafa' : '#94a3b8' } },
    { id: 'gpt-gemma', source: 'gpt', target: 'gemma', label: gemmaActive ? 'Observe · processing' : recentResult ? 'Result · recently completed' : 'Observe · advisory',
      animated: gemmaActive || recentResult, selectable: false, style: { stroke: gemmaActive || recentResult ? '#e2ba69' : '#64748b', strokeDasharray: '5 5' },
      labelStyle: { fill: gemmaActive || recentResult ? '#e2ba69' : '#94a3b8' } },
  ], [handoffActive, delegateActive, localActive, gemmaActive, recentResult, selectedId, items]);
  const nodes = allNodes.filter(node => ['chatgpt', 'gpt', 'claude'].includes(node.id) || services[node.id as keyof typeof services]);
  const visibleIds = new Set(nodes.map(node => node.id));
  const edges = allEdges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
  const nodeKey = nodes.map(node => node.id).join(',');
  useEffect(() => { if (instance) void instance.fitView({ padding: 0.12, maxZoom: 1, duration: 0 }); }, [instance, nodeKey]);
  return <section className="panel flow-panel" aria-label="Current orchestration flow">
    <div className="panel-heading"><div><h2>Live orchestration flow</h2><p>Core tiles always visible · connectors reflect observed activity</p></div>
      <span className="flow-legend"><i className="dot" style={{ background: '#64748b' }} /> Idle <i className="dot teal" /> Request <i className="dot violet" /> Return</span></div>
    <div className="flow-canvas" ref={canvasRef} role="img" aria-label={nodes.map(node => node.id === 'chatgpt' ? 'ChatGPT' : node.id === 'gpt' ? 'GPT / Codex' : node.id === 'qwen' ? 'Skynet / Qwen' : node.id === 'gemma' ? 'Gemma advisory observer' : 'Claude').join(' · ')}>
      <ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
        onInit={setInstance}
        panActivationKeyCode={null}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable
        onEdgeClick={(_, edge) => { if (edge.id === 'chatgpt-gpt') onHandoffs(); else if (edge.id !== 'gpt-gemma') onEdge(edge.id === 'claude-qwen' ? 'routed' : 'not_routed'); }}
        proOptions={{ hideAttribution: true }}>
        <Background color="#1a4770" gap={22} size={1} /><Controls showInteractive={false} />
      </ReactFlow>
    </div>
  </section>;
}
