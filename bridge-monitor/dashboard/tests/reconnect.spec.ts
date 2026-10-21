import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('initial snapshot establishes the stream cursor before loading the work list', async ({ page, request }) => {
  let releaseSnapshot!: () => void;
  const snapshotGate = new Promise<void>(resolve => { releaseSnapshot = resolve; });
  let snapshotStarted!: () => void;
  const snapshotSeen = new Promise<void>(resolve => { snapshotStarted = resolve; });
  await page.route('**/api/v1/snapshot?**', async route => {
    snapshotStarted();
    await snapshotGate;
    await route.continue();
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await snapshotSeen;
  await page.waitForTimeout(250);

  const unique = randomUUID();
  const taskId = `initial-task-${unique}`;
  const workId = `initial-work-${unique}`;
  const base = {
    schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
    occurred_at: new Date().toISOString(), origin: 'observed',
    observation_layer: 'e2e', capture_completeness: 'complete',
  };
  const response = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      { ...base, event_id: `initial-task-event-${unique}`, producer_sequence: 1,
        event_type: 'task.created', task_id: taskId, payload: {} },
      { ...base, event_id: `initial-work-event-${unique}`, producer_sequence: 2,
        event_type: 'work_item.created', task_id: taskId, work_item_id: workId,
        payload: { category: `initial-work-${unique}` } },
    ], content: [] },
  });
  expect(response.ok()).toBe(true);
  releaseSnapshot();
  await expect(page.locator('.connection')).toContainText('Connected');
  await expect(page.locator('tr').filter({ hasText: workId })).toBeVisible();
});

test('committed events update the dashboard without reopening the live stream', async ({ page, request }) => {
  const streamRequests: string[] = [];
  page.on('request', event => {
    if (new URL(event.url()).pathname === '/api/v1/stream') streamRequests.push(event.url());
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.locator('.connection')).toContainText('Connected');
  await expect.poll(() => streamRequests.length).toBe(1);

  const unique = randomUUID();
  for (let index = 0; index < 3; index += 1) {
    const category = `stream-continuity-${index}-${unique}`;
    const taskId = `stream-task-${index}-${unique}`;
    const base = {
      schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
      occurred_at: new Date().toISOString(), origin: 'observed',
      observation_layer: 'e2e', capture_completeness: 'complete',
    };
    const response = await request.post('/api/v1/events/batch', {
      headers: { Authorization: 'Bearer producer-secret' },
      data: { events: [
        { ...base, event_id: `stream-task-event-${index}-${unique}`, producer_sequence: index * 2 + 1,
          event_type: 'task.created', task_id: taskId, payload: {} },
        { ...base, event_id: `stream-work-event-${index}-${unique}`, producer_sequence: index * 2 + 2,
          event_type: 'work_item.created', task_id: taskId, work_item_id: `stream-work-${index}-${unique}`,
          payload: { category } },
      ], content: [] },
    });
    expect(response.ok()).toBe(true);
    await expect(page.getByText(category).first()).toBeVisible();
  }
  expect(streamRequests).toHaveLength(1);
});

test('reload resumes an authenticated snapshot and stream', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.getByRole('heading', { name: 'Live orchestration flow' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause display' }).click();
  await expect(page.getByRole('button', { name: 'Resume display' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Live orchestration flow' })).toBeVisible();
  await expect(page.getByRole('status').first()).toContainText(/connected|connecting|offline/i);
});

test('paused display keeps collecting and resumes with a new committed work item', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  const pendingMetric = page.getByRole('button', { name: /Routing pending/ });
  await expect(page.locator('.connection')).toContainText('Connected');
  await expect(pendingMetric.locator('small')).toHaveText(/^\d+ \/ \d+$/);
  const before = await pendingMetric.locator('small').innerText();
  const match = before.match(/^(\d+) \/ (\d+)$/);
  expect(match).not.toBeNull();
  const beforePending = Number(match![1]);
  const beforeTotal = Number(match![2]);
  await page.getByRole('button', { name: 'Pause display' }).click();
  const occurredAt = new Date().toISOString();
  const unique = randomUUID();
  const base = { schema_version: 1, producer_id: 'seed', producer_incarnation: `browser-live-test-${unique}`,
    occurred_at: occurredAt, origin: 'observed', observation_layer: 'e2e', capture_completeness: 'complete' };
  const response = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      { ...base, event_id: `e2e-task-created-${unique}`, producer_sequence: 1,
        event_type: 'task.created', task_id: `e2e-task-${unique}`, payload: {} },
      { ...base, event_id: `e2e-work-created-${unique}`, producer_sequence: 2,
        event_type: 'work_item.created', task_id: `e2e-task-${unique}`, work_item_id: `e2e-work-${unique}`,
        payload: { category: 'live-browser-check' } },
    ], content: [] },
  });
  expect(response.ok()).toBe(true);
  await expect(page.getByText(/new events received/i)).toBeVisible();
  await expect(pendingMetric.locator('small')).toHaveText(before);
  await page.getByRole('button', { name: 'Resume display' }).click();
  await expect(pendingMetric.locator('small')).toHaveText(`${beforePending + 1} / ${beforeTotal + 1}`);
  await expect(page.getByText('live-browser-check').first()).toBeVisible();
});

test('paused display freezes the selected exchange until resume', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await expect(page.locator('.connection')).toContainText('Connected');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  const inspector = page.getByRole('complementary', { name: 'Selected work item' });
  await expect(inspector).toContainText('Qwen original');
  const detailResponse = await page.request.get('/api/v1/work-items/demo-work-061');
  expect(detailResponse.ok()).toBe(true);
  const detail = await detailResponse.json();
  const localCall = detail.calls.find((call: { local_dispatch: boolean }) => call.local_dispatch);
  expect(localCall).toBeTruthy();

  const unique = randomUUID();
  const message = `paused-detail-message-${unique}`;
  const content = `paused-detail-content-${unique}`;
  const beforePause = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      {
        schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
        producer_sequence: 1, event_id: `paused-detail-1-${unique}`,
        event_type: 'attempt.status', occurred_at: new Date().toISOString(),
        origin: 'synthetic', observation_layer: 'bridge', capture_completeness: 'complete',
        task_id: 'demo-task-001', work_item_id: 'demo-work-061',
        call_id: localCall.call_id, attempt_id: `${localCall.call_id}:attempt:1`,
        payload: { agent_state: 'streaming', agent_reachable: true },
      },
      {
        schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
        producer_sequence: 2, event_id: `paused-detail-2-${unique}`,
        event_type: 'response.delta', occurred_at: new Date().toISOString(),
        origin: 'synthetic', observation_layer: 'bridge', capture_completeness: 'complete',
        task_id: 'demo-task-001', work_item_id: 'demo-work-061',
        call_id: localCall.call_id, attempt_id: `${localCall.call_id}:attempt:1`,
        payload: { message_id: message, chunk_sequence: 1, content_id: content },
      },
    ], content: [{ content_id: content, raw_base64: Buffer.from('partial').toString('base64'),
      role: 'assistant', direction: 'in', content_type: 'text/plain', origin: 'synthetic',
      capture_layer: 'bridge', message_id: message }] },
  });
  expect(beforePause.ok()).toBe(true);
  const observation = inspector.locator('.inspector-summary p').filter({ hasText: 'Last bridge observation' });
  await expect(observation).toContainText('Skynet job: streaming');
  await expect(observation).toContainText(/Last Qwen output \d+s ago/);
  await expect(page.locator('.connection')).toContainText('Connected');
  await page.waitForTimeout(300);
  await page.getByRole('button', { name: 'Pause display' }).click();
  const frozenObservation = await observation.innerText();
  const response = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [{
      schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
      producer_sequence: 3, event_id: `paused-detail-3-${unique}`,
      event_type: 'attempt.status', occurred_at: new Date().toISOString(),
      origin: 'synthetic', observation_layer: 'bridge', capture_completeness: 'complete',
      task_id: 'demo-task-001', work_item_id: 'demo-work-061',
      call_id: localCall.call_id, attempt_id: `${localCall.call_id}:attempt:1`,
      payload: { agent_state: 'ollama_connecting', agent_reachable: true },
    }], content: [] },
  });
  expect(response.ok()).toBe(true);
  await expect(page.getByText(/new events received/i)).toBeVisible();
  await page.waitForTimeout(1800);
  await expect(observation).toHaveText(frozenObservation);
  await page.getByRole('button', { name: 'Resume display' }).click();
  await expect(inspector).toContainText('Skynet job: ollama_connecting');
});
