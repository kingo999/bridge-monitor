const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function load(relative, overrides = {}) {
  const filename = path.resolve(__dirname, '..', relative);
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  const localRequire = id => {
    if (id in overrides) return overrides[id];
    if (id.startsWith('.')) return load(path.relative(path.resolve(__dirname, '..'), path.resolve(path.dirname(filename), id + '.tsx')), overrides);
    return require(id);
  };
  new Function('require', 'module', 'exports', compiled)(localRequire, module, module.exports);
  return module.exports;
}

const activity = () => load('lib/flowActivity.ts');
const now = Date.parse('2026-11-04T18:00:00Z');
test('a recent handoff expires independently of subsequent polling', () => {
  const { handoffActivityRemaining } = activity();
  const at = new Date(now).toISOString();
  assert.equal(handoffActivityRemaining(at, now, true), 20000);
  assert.equal(handoffActivityRemaining(at, now + 19999, true), 1);
  assert.equal(handoffActivityRemaining(at, now + 20000, true), 0);
});
test('historical, invalid, future or unavailable handoffs are idle', () => {
  const { handoffActivityRemaining } = activity();
  for (const at of [undefined, 'bad', new Date(now - 30000).toISOString(), new Date(now + 1).toISOString()]) {
    assert.equal(handoffActivityRemaining(at, now, true), 0);
  }
  assert.equal(handoffActivityRemaining(new Date(now).toISOString(), now, false), 0);
});

function graph({ active = 0, queued = 0, ready = false, paused = false, handoffTime, handoffAvailable = true, handoffError = '' } = {}) {
  let config;
  const FlowGraph = load('components/FlowGraph.tsx', {
    '@xyflow/react': {
      ReactFlow: props => { config = props; return React.createElement('div', null, props.nodes.map(n => React.createElement('div', { key: n.id }, n.data.label))); },
      Background: () => null, Controls: () => null,
      Position: { Right: 'right', Left: 'left' }, MarkerType: { ArrowClosed: 'arrowclosed' },
    },
    '@/lib/useHandoffPage': { useHandoffPage: () => ({ page: { available: handoffAvailable, total: 12, rows: handoffTime ? [{ time: handoffTime }] : [] }, error: handoffError }) },
    '@/lib/useServiceAvailability': { useServiceAvailability: () => ({ claude: ready, qwen: false, gemma: false }) },
    '@/lib/useClassifierActivity': { useClassifierActivity: () => ({ activity: null, failed: false }) },
    './ClassifierActivity': { default: () => null },
    '@/lib/flowActivity': activity(),
  }).default;
  const html = renderToStaticMarkup(React.createElement(FlowGraph, {
    status: { active, queued }, capacity: null, items: [], executions: [], selectedId: null,
    expanded: true, paused, onToggle() {}, onSelect() {}, onEdge() {}, onHandoffs() {},
  }));
  return { ...config, html };
}
test('core tiles and both links remain visible when Claude is unavailable', () => {
  const rendered = graph();
  assert.deepEqual(rendered.nodes.map(n => n.id), ['chatgpt', 'gpt', 'claude']);
  assert.deepEqual(rendered.edges.map(e => e.id), ['chatgpt-gpt', 'gpt-claude']);
  assert.match(rendered.html, /Activity unavailable/);
});
test('idle and queued-only work leaves both links grey and still clickable', () => {
  const rendered = graph({ ready: true, queued: 2 });
  for (const edge of rendered.edges) {
    assert.equal(edge.style.stroke, '#64748b');
    assert.equal(edge.animated, false);
    assert.equal(edge.markerEnd.color, '#64748b');
  }
  assert.equal(typeof rendered.onEdgeClick, 'function');
});
test('running Claude highlights Delegate without highlighting historical Handoffs', () => {
  const edges = graph({ ready: true, active: 1, handoffTime: '2020-01-01T00:00:00Z' }).edges;
  assert.equal(edges.find(e => e.id === 'gpt-claude').style.stroke, '#05d7c0');
  assert.equal(edges.find(e => e.id === 'gpt-claude').animated, true);
  assert.equal(edges.find(e => e.id === 'chatgpt-gpt').style.stroke, '#64748b');
});
test('a fresh handoff highlights only Handoffs', () => {
  const edges = graph({ ready: true, handoffTime: new Date().toISOString() }).edges;
  assert.equal(edges.find(e => e.id === 'chatgpt-gpt').style.stroke, '#05d7c0');
  assert.equal(edges.find(e => e.id === 'chatgpt-gpt').animated, true);
  assert.equal(edges.find(e => e.id === 'gpt-claude').style.stroke, '#64748b');
});
test('paused or unavailable Claude never animates stale active counts', () => {
  for (const options of [{ ready: true, paused: true }, { ready: false }]) {
    const edges = graph({ active: 1, handoffTime: '2020-01-01T00:00:00Z', ...options }).edges;
    const delegate = edges.find(e => e.id === 'gpt-claude');
    assert.ok(delegate);
    assert.equal(delegate.style.stroke, '#64748b');
    assert.equal(delegate.animated, false);
  }
});
test('paused, failed and unavailable handoff observations suppress a fresh record', () => {
  for (const options of [{ paused: true }, { handoffAvailable: false }, { handoffError: 'unavailable' }]) {
    const edge = graph({ ready: true, handoffTime: new Date().toISOString(), ...options }).edges.find(e => e.id === 'chatgpt-gpt');
    assert.equal(edge.style.stroke, '#64748b');
    assert.equal(edge.animated, false);
  }
});
