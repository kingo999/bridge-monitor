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
  localReady = false, modelSettings = {}, executions = [], expanded = true, codexModels, chatSelection, chatActivity,
  capacity = null, classifier = null } = {}) {
  let config;
  const FlowGraph = load('components/FlowGraph.tsx', {
    '@xyflow/react': {
      ReactFlow: props => { config = props; return React.createElement('div', null, props.nodes.map(n => React.createElement('div', { key: n.id }, n.data.label))); },
      Background: () => null, Controls: () => null,
      Position: { Right: 'right', Left: 'left' }, MarkerType: { ArrowClosed: 'arrowclosed' },
    },
    '@/lib/useHandoffPage': { useHandoffPage: () => ({ page: { available: handoffAvailable, total: 12, rows: handoffTime ? [{ time: handoffTime }] : [], chat_selection: chatSelection, chat_activity: chatActivity }, error: handoffError }) },
    '@/lib/useServiceAvailability': { useServiceAvailability: () => ({ claude: ready, qwen: localReady, gemma: localReady, modelSettings, codexModels }) },
    '@/lib/useClassifierActivity': { useClassifierActivity: () => ({ activity: classifier, failed: false }) },
    '@/lib/flowActivity': activity(),
  }).default;
  const html = renderToStaticMarkup(React.createElement(FlowGraph, {
    status: { active, queued }, capacity, items: [], executions, selectedId: null,
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

const tileHtml = (rendered, id) => renderToStaticMarkup(rendered.nodes.find(node => node.id === id).data.label);
const glows = html => /class="[^"]*\bis-working\b/.test(html);
const preparing = (since = new Date().toISOString()) =>
  ({ state: 'preparing', workflow_id: 'wf', iteration: 2, since, expires_in_seconds: 300 });

test('ChatGPT preparing expires after a short window and ignores other states', () => {
  const { chatPreparingRemaining } = activity();
  const since = new Date(now).toISOString();
  assert.equal(chatPreparingRemaining(preparing(since), now, true), 300000);
  assert.equal(chatPreparingRemaining(preparing(since), now + 299999, true), 1);
  assert.equal(chatPreparingRemaining(preparing(since), now + 300000, true), 0);
  assert.equal(chatPreparingRemaining(preparing(since), now, false), 0);
  assert.equal(chatPreparingRemaining({ ...preparing(since), since: 'bad' }, now, true), 0);
  assert.equal(chatPreparingRemaining({ ...preparing(since), expires_in_seconds: 86400 }, now, true), 300000);
  assert.equal(chatPreparingRemaining({ ...preparing(since), state: 'awaiting_decision' }, now, true), 0);
  assert.equal(chatPreparingRemaining(undefined, now, true), 0);
});
test('ChatGPT glows while preparing a retrieved review and stops once delivery is confirmed', () => {
  const working = tileHtml(graph({ ready: true, chatActivity: preparing() }), 'chatgpt');
  assert.ok(glows(working));
  assert.match(working, /data-state="working"[^>]*>.*Preparing response/);
  const delivered = tileHtml(graph({ ready: true, chatActivity: { state: 'awaiting_decision', workflow_id: 'wf',
    iteration: 2, since: new Date().toISOString() } }), 'chatgpt');
  assert.ok(!glows(delivered));
  assert.match(delivered, /Awaiting your decision/);
});
test('ChatGPT stays unlit before the first handover, after expiry, and when paused or unavailable', () => {
  const unobserved = tileHtml(graph({ ready: true }), 'chatgpt');
  assert.ok(!glows(unobserved));
  assert.doesNotMatch(unobserved, /node-status/);
  const expired = tileHtml(graph({ ready: true, chatActivity: preparing(new Date(Date.now() - 300001).toISOString()) }), 'chatgpt');
  assert.ok(!glows(expired));
  assert.doesNotMatch(expired, /Preparing response/);
  for (const options of [{ paused: true }, { handoffAvailable: false }, { handoffError: 'unavailable' }]) {
    const html = tileHtml(graph({ ready: true, chatActivity: preparing(), ...options }), 'chatgpt');
    assert.ok(!glows(html));
    assert.doesNotMatch(html, /Preparing response/);
  }
});
test('ChatGPT activity leaves the Handoffs and Delegate arrows on their own signals', () => {
  const edges = graph({ ready: true, chatActivity: preparing(), handoffTime: '2020-01-01T00:00:00Z' }).edges;
  for (const edge of edges) {
    assert.equal(edge.style.stroke, '#64748b');
    assert.equal(edge.animated, false);
  }
});
test('Codex glows during an observed desktop turn and keeps waiting while a worker runs', () => {
  const codexModels = [{ model: 'gpt-6.1-sol', effort: 'high' }];
  const working = tileHtml(graph({ ready: true, codexModels }), 'gpt');
  assert.ok(glows(working));
  assert.match(working, /data-state="working"/);
  for (const options of [{ active: 1 }, { queued: 1 }]) {
    const waiting = tileHtml(graph({ ready: true, codexModels, ...options }), 'gpt');
    assert.ok(!glows(waiting));
    assert.match(waiting, /is-waiting/);
    assert.match(waiting, /Waiting for bridge workers/);
  }
  assert.ok(!glows(tileHtml(graph({ ready: true }), 'gpt')));
  assert.ok(!glows(tileHtml(graph({ ready: true, codexModels, paused: true }), 'gpt')));
});
test('Claude, Skynet and Gemma glow only for observed execution', () => {
  const capacity = qwen => ({ claude: { active: 0, queued: 0, held_uncertain: 0 }, qwen: { held_uncertain: 0, ...qwen } });
  const gemma = counts => ({ available: true, classifying: 0, queued: 0, uncertain: 0, checked_at: new Date().toISOString(), ...counts });
  assert.ok(glows(tileHtml(graph({ ready: true, active: 1 }), 'claude')));
  for (const options of [{ queued: 2 }, {}, { active: 1, paused: true }, { active: 1, ready: false }]) {
    assert.ok(!glows(tileHtml(graph({ ready: true, ...options }), 'claude')), JSON.stringify(options));
  }
  const local = (qwen, extra = {}) => tileHtml(graph({ ready: true, localReady: true, capacity: capacity(qwen), ...extra }), 'qwen');
  assert.ok(glows(local({ active: 1, queued: 0 })));
  assert.ok(!glows(local({ active: 0, queued: 2 })));
  assert.ok(!glows(local({ active: 0, queued: 0 })));
  assert.ok(!glows(local({ active: 1, queued: 0 }, { paused: true })));
  const classifier = (counts, extra = {}) => tileHtml(graph({ ready: true, localReady: true, classifier: gemma(counts), ...extra }), 'gemma');
  assert.ok(glows(classifier({ classifying: 1 })));
  assert.ok(!glows(classifier({ queued: 1 })));
  assert.ok(!glows(classifier({})));
  assert.ok(!glows(classifier({ classifying: 1 }, { paused: true })));
  assert.ok(!glows(classifier({ classifying: 1, available: false })));
});
