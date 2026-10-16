'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Position, ReactFlow, type Edge, type Node, type ReactFlowInstance } from '@xyflow/react';
import type { DashboardStatus, PoolCapacity, WorkerExecution, WorkItem } from '@/lib/contracts.generated';
import WorkerGroup from './WorkerGroup';

export default function FlowGraph({ status, capacity, items, executions, selectedId, expanded, onToggle, onSelect, onEdge }: {
  status: DashboardStatus | null; capacity: Record<'claude' | 'qwen', PoolCapacity> | null;
  items: WorkItem[]; executions: WorkerExecution[]; selectedId: string | null;
  expanded: boolean; onToggle: () => void; onSelect: (id: string) => void;
  onEdge: (route: 'routed' | 'not_routed') => void;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [instance, setInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const active = capacity?.claude.active ?? status?.active ?? 0;
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
  const nodes = useMemo<Node[]>(() => [
    { id: 'gpt', position: { x: 0, y: 85 }, sourcePosition: Position.Right, targetPosition: Position.Left, data: { label: <div className="flow-node origin-node"><div className="node-orb gpt">◎</div><strong>GPT / Codex</strong><small>Main conversation</small></div> }, type: 'default', draggable: false, style: { width: 160 } },
    { id: 'claude', position: { x: 215, y: 0 }, data: { label: <WorkerGroup active={active} queued={capacity?.claude.queued ?? status?.queued ?? 0}
      held={capacity?.claude.held_uncertain ?? status?.held_uncertain ?? 0} blocked={status?.blocked ?? 0} executions={executions}
      expanded={expanded} onToggle={onToggle} onSelect={onSelect} /> }, type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 250 } },
    { id: 'qwen', position: { x: 515, y: 85 }, data: { label: <div className="flow-node skynet-node"><div className="node-orb qwen">✦</div><strong>Skynet / Qwen</strong><small>RTX 4080 Super · local inference</small>
      {capacity && <small>{capacity.qwen.active} active · {capacity.qwen.queued} queued · {capacity.qwen.held_uncertain} held uncertain</small>}</div> }, type: 'default', draggable: false, sourcePosition: Position.Right, targetPosition: Position.Left, style: { width: 170 } },
  ], [active, status, capacity, executions, expanded, onToggle, onSelect]);
  const edges = useMemo<Edge[]>(() => [
    { id: 'gpt-claude', source: 'gpt', target: 'claude', label: 'Delegate', animated: active > 0,
      style: { stroke: '#05d7c0', strokeWidth: selectedId ? 3 : 2 }, labelStyle: { fill: '#91d7d4' } },
    { id: 'claude-qwen', source: 'claude', target: 'qwen', label: 'Request / return', animated: active > 0,
      style: { stroke: '#9a80f5', strokeWidth: selectedId && items.find(x => x.work_item_id === selectedId)?.routed ? 3 : 2 },
      labelStyle: { fill: '#b9aafa' } },
  ], [active, selectedId, items]);
  return <section className="panel flow-panel" aria-label="Current orchestration flow">
    <div className="panel-heading"><div><h2>Live orchestration flow</h2><p>Current topology · grouped Claude workers</p></div>
      <span className="flow-legend"><i className="dot teal" /> Request <i className="dot violet" /> Return</span></div>
    <div className="flow-canvas" ref={canvasRef} role="img" aria-label="GPT and Codex delegate to Claude workers, who may delegate to Skynet Qwen">
      <ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.12 }}
        onInit={setInstance}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable
        onEdgeClick={(_, edge) => onEdge(edge.id === 'claude-qwen' ? 'routed' : 'not_routed')}
        proOptions={{ hideAttribution: true }}>
        <Background color="#1a4770" gap={22} size={1} /><Controls showInteractive={false} />
      </ReactFlow>
    </div>
  </section>;
}
