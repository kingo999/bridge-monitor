'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Position, ReactFlow, type Edge, type Node, type ReactFlowInstance } from '@xyflow/react';
import type { DashboardStatus, PoolCapacity, WorkerExecution, WorkItem } from '@/lib/contracts.generated';
import { MarkerType } from '@xyflow/react';
import { useHandoffPage } from '@/lib/useHandoffPage';
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
  const { page: handoffs, error: handoffError } = useHandoffPage('limit=0', !paused);
  const total = handoffs?.total;
  const services = useServiceAvailability(paused);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [instance, setInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const active = capacity?.claude.active ?? status?.active ?? 0;
  const claudeQueued = capacity?.claude.queued ?? status?.queued ?? 0;
  const qwenActive = capacity?.qwen.active ?? 0;
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
        <button className="nodrag chatgpt-handoffs" type="button" onClick={onHandoffs} aria-label="Open Design Mode handoff audit">
          {total != null ? `${total.toLocaleString()} handoff${total === 1 ? '' : 's'}` :
            handoffError || handoffs ? 'Count unavailable' : 'View handoffs'}
        </button></div> } },
    { id: 'gpt', position: { x: 0, y: 85 }, sourcePosition: Position.Right, targetPosition: Position.Left, data: { label: <div className={`flow-node origin-node${nodeActivityClass(originState)}`}><div className="node-orb gpt">◎</div><strong>GPT / Codex</strong><small>Main conversation</small>
      <NodeActivityStatus state={originState} label={originState === 'waiting' ? 'Waiting for bridge workers' : undefined} /></div> }, type: 'default', draggable: false, style: { width: 160 } },
    { id: 'claude', position: { x: 215, y: 0 }, data: { label: <WorkerGroup active={active} queued={claudeQueued}
      held={capacity?.claude.held_uncertain ?? status?.held_uncertain ?? 0} blocked={status?.blocked ?? 0} executions={executions}
      expanded={expanded} onToggle={onToggle} onSelect={onSelect} paused={paused} available={!!capacity || !!status} /> }, type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 250 } },
    { id: 'qwen', position: { x: 515, y: 85 }, data: { label: <div className={`flow-node skynet-node${nodeActivityClass(qwenState)}`}><div className="node-orb qwen">✦</div><strong>Skynet / Qwen</strong><small>RTX 4080 Super · local inference</small>
      <NodeActivityStatus state={qwenState} queued={capacity?.qwen.queued ?? 0} />
      {capacity && <small>{capacity.qwen.active} active · {capacity.qwen.queued} queued · {capacity.qwen.held_uncertain} held uncertain</small>}</div> }, type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 170 } },
    { id: 'gemma', position: { x: 215, y: 265 }, data: { label: <ClassifierActivity activity={activity} failed={failed} paused={paused} /> },
      type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 250 } },
  ], [total, handoffError, handoffs, onHandoffs, active, claudeQueued, qwenActive, originState, qwenState, status, capacity, executions, expanded, onToggle, onSelect, activity, failed, paused]);
  const allEdges = useMemo<Edge[]>(() => [
    { id: 'chatgpt-gpt', source: 'chatgpt', target: 'gpt', label: 'Handoffs',
      markerEnd: { type: MarkerType.ArrowClosed, color: '#05d7c0' }, style: { stroke: '#05d7c0', strokeWidth: 2 } },
    { id: 'gpt-claude', source: 'gpt', target: 'claude', label: 'Delegate', animated: !paused && active > 0,
      style: { stroke: '#05d7c0', strokeWidth: selectedId ? 3 : 2 }, labelStyle: { fill: '#91d7d4' } },
    { id: 'claude-qwen', source: 'claude', target: 'qwen', label: 'Request / return', animated: !paused && qwenActive > 0,
      style: { stroke: '#9a80f5', strokeWidth: selectedId && items.find(x => x.work_item_id === selectedId)?.routed ? 3 : 2 },
      labelStyle: { fill: '#b9aafa' } },
    { id: 'gpt-gemma', source: 'gpt', target: 'gemma', label: gemmaActive ? 'Observe · processing' : recentResult ? 'Result · recently completed' : 'Observe · advisory',
      animated: gemmaActive || recentResult, selectable: false, style: { stroke: '#e2ba69', strokeDasharray: '5 5' },
      labelStyle: { fill: '#e2ba69' } },
  ], [active, qwenActive, gemmaActive, recentResult, paused, selectedId, items]);
  const nodes = allNodes.filter(node => node.id === 'chatgpt' || node.id === 'gpt' || services[node.id as keyof typeof services]);
  const visibleIds = new Set(nodes.map(node => node.id));
  const edges = allEdges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
  const nodeKey = nodes.map(node => node.id).join(',');
  useEffect(() => { if (instance) void instance.fitView({ padding: 0.12, maxZoom: 1, duration: 0 }); }, [instance, nodeKey]);
  return <section className="panel flow-panel" aria-label="Current orchestration flow">
    <div className="panel-heading"><div><h2>Live orchestration flow</h2><p>Recorded worker activity · available configured services</p></div>
      <span className="flow-legend"><i className="dot teal" /> Request <i className="dot violet" /> Return</span></div>
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
