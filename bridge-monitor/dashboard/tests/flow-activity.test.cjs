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

function graph({ active = 0, queued = 0, ready = false, paused = false, handoffTime, handoffAvailable = true, handoffError = '',
  localReady = false, modelSettings = {}, executions = [], expanded = true, codexModels, chatSelection } = {}) {
  let config;
  const FlowGraph = load('components/FlowGraph.tsx', {
    '@xyflow/react': {
      ReactFlow: props => { config = props; return React.createElement('div', null, props.nodes.map(n => React.createElement('div', { key: n.id }, n.data.label))); },
      Background: () => null, Controls: () => null,
      Position: { Right: 'right', Left: 'left' }, MarkerType: { ArrowClosed: 'arrowclosed' },
    },
    '@/lib/useHandoffPage': { useHandoffPage: () => ({ page: { available: handoffAvailable, total: 12, rows: handoffTime ? [{ time: handoffTime }] : [], chat_selection: chatSelection }, error: handoffError }) },
    '@/lib/useServiceAvailability': { useServiceAvailability: () => ({ claude: ready, qwen: localReady, gemma: localReady, modelSettings, codexModels }) },
    '@/lib/useClassifierActivity': { useClassifierActivity: () => ({ activity: null, failed: false }) },
    '@/lib/flowActivity': activity(),
  }).default;
  const html = renderToStaticMarkup(React.createElement(FlowGraph, {
    status: { active, queued }, capacity: null, items: [], executions, selectedId: null,
    expanded, paused, onToggle() {}, onSelect() {}, onEdge() {}, onHandoffs() {},
  }));
  return { ...config, html };
}
test('core tiles and both links remain visible when Claude is unavailable', () => {
  const rendered = graph();
  assert.deepEqual(rendered.nodes.map(n => n.id), ['chatgpt', 'gpt', 'claude']);
  assert.deepEqual(rendered.edges.map(e => e.id), ['chatgpt-gpt', 'gpt-claude']);
  assert.match(rendered.html, /Activity unavailable/);
});

test('the five tiles distinguish concurrent Claude calls, configured models and missing metadata', () => {
  const { nodes } = graph({ active: 2, ready: true, localReady: true, expanded: false,
    modelSettings: {
      claude: [{ model: 'claude-unused', effort: 'medium' }],
      qwen: [{ model: 'qwen-test:32b', effort: null }],
      gemma: [{ model: 'gemma-test:12b', effort: 'Thinking off' }],
    },
    executions: [
      { call_id: 'first', reported_model: 'claude-haiku-test', requested_model: 'claude-haiku-test', configured_effort: 'low', active: true },
      { call_id: 'second', reported_model: null, requested_model: 'claude-opus-test', configured_effort: 'medium', active: true },
      { call_id: 'held', reported_model: 'claude-held', active: false },
    ],
  });
  const tile = id => renderToStaticMarkup(nodes.find(node => node.id === id).data.label);
  assert.match(tile('chatgpt'), /Model and effort unavailable/);
  assert.match(tile('gpt'), /Model and effort unavailable/);
  assert.match(tile('claude'), /claude-haiku-test · low effort/);
  assert.match(tile('claude'), /Requested · claude-opus-test · medium effort/);
  assert.doesNotMatch(tile('claude'), /claude-held|claude-unused/);
  assert.match(tile('qwen'), /Configured · qwen-test:32b · Effort unavailable/);
  assert.match(tile('gemma'), /Configured · gemma-test:12b · Thinking off/);
});

test('idle and queued-only Claude omit model labels while active calls with missing detail show unavailable', () => {
  const modelSettings = { claude: [{ model: 'claude-idle', effort: 'medium' }] };
  for (const queued of [0, 2]) {
    const idle = graph({ ready: true, modelSettings, queued }).nodes.find(node => node.id === 'claude');
    assert.doesNotMatch(renderToStaticMarkup(idle.data.label), /flow-model-details|claude-idle/);
  }
  const running = graph({ active: 1, ready: true, modelSettings }).html;
  assert.doesNotMatch(running, /claude-idle/);
  assert.match(running, /Claude workers[\s\S]*Model and effort unavailable/);
});

test('ChatGPT shows selected settings from the last handoff and Codex shows observed turn settings', () => {
  const { nodes } = graph({ chatSelection: { model: 'GPT-6 Sol', effort: 'Extra High', time: '2026-11-05T10:00:00Z' },
    codexModels: [{ model: 'gpt-6.1-sol', effort: 'high' }, { model: 'gpt-6-luna', effort: null },
      { model: 'gpt-6.1-terra', effort: 'medium', effort_source: 'last_configured' }] });
  const tile = id => renderToStaticMarkup(nodes.find(node => node.id === id).data.label);
  assert.match(tile('chatgpt'), /Last handoff · GPT-6 Sol · Extra High effort/);
  assert.match(tile('gpt'), /gpt-6\.1-sol · high effort/);
  assert.match(tile('gpt'), /gpt-6-luna · Effort unavailable/);
  assert.match(tile('gpt'), /gpt-6\.1-terra · medium effort · Last configured/);
  assert.doesNotMatch(tile('gpt'), /gpt-6\.1-sol · high effort · Last configured/);
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
