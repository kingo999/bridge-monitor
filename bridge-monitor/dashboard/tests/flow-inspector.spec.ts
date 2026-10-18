import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('synthetic exchange has grouped flow and side-by-side response comparison', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await expect(page.getByRole('button', { name: /Claude workers/ })).toBeVisible();
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  await expect(page.getByText('Qwen original')).toBeVisible();
  await expect(page.getByText('What is 2 + 2?').first()).toBeVisible();
  await expect(page.getByRole('tab', { name: 'decision' })).toBeVisible();
  await page.getByRole('tab', { name: 'timeline' }).click();
  await expect(page.getByText('call.dispatched')).toBeVisible();
  await expect(page.locator('.react-flow__node').first()).toBeVisible();
  await page.screenshot({ path: '../var/reports/dashboard/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 740, height: 1050 });
  await expect(page.getByRole('heading', { name: 'Live orchestration flow' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Selected work item' })).toBeVisible();
  await expect(page.locator('.react-flow__node').first()).toBeVisible();
  await page.screenshot({ path: '../var/reports/dashboard/narrow.png', fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test('long timeline marks the omitted middle between early and recent events', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.route('**/api/v1/work-items/demo-work-061', async route => {
    const response = await route.fetch();
    const detail = await response.json();
    const sample = detail.events[0];
    detail.events = Array.from({ length: 500 }, (_, index) => ({
      ...sample, event_id: `timeline-${index}`, event_type: `timeline-${index}`,
    }));
    detail.events_truncated = true;
    detail.events_gap_after = 100;
    await route.fulfill({ response, json: detail });
  });
  await page.getByLabel('Origin').selectOption('synthetic');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  await page.getByRole('tab', { name: 'timeline' }).click();
  const timeline = page.locator('ol.timeline');
  await expect(timeline.locator('li')).toHaveCount(501);
  await expect(timeline.locator('li').nth(99)).toContainText('timeline-99');
  await expect(timeline.locator('li').nth(100)).toContainText('Middle events omitted');
  await expect(timeline.locator('li').last()).toContainText('timeline-499');
});

test('exchange inspector shows model usage with reported and unknown cache counts', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  const detailResponse = await page.request.get('/api/v1/work-items/demo-work-061');
  expect(detailResponse.ok()).toBe(true);
  const detail = await detailResponse.json();
  const model = detail.models[0];
  const nonce = randomUUID();
  const base = { schema_version: 1, producer_id: 'seed', producer_incarnation: nonce,
    occurred_at: new Date().toISOString(), origin: 'synthetic',
    observation_layer: 'e2e', capture_completeness: 'complete',
    task_id: 'demo-task-001', work_item_id: 'demo-work-061',
    call_id: model.call_id, attempt_id: model.attempt_id,
    model_execution_id: model.model_execution_id, event_type: 'usage.reported' };
  const ingested = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      { ...base, event_id: `${nonce}-usage-1`, producer_sequence: 1,
        payload: { observation_id: `${nonce}-observation-1`, scope: 'execution',
          is_cumulative: true, input_tokens: 4, cache_read_input_tokens: 2440,
          cache_creation_input_tokens: 0, output_tokens: 80,
          provenance: 'synthetic_usage' } },
      { ...base, event_id: `${nonce}-usage-2`, producer_sequence: 2,
        payload: { observation_id: `${nonce}-observation-2`, scope: 'execution',
          is_cumulative: false, input_tokens: 0, output_tokens: 3,
          provenance: 'synthetic_usage' } },
    ], content: [] },
  });
  expect(ingested.ok()).toBe(true);
  await page.getByLabel('Origin').selectOption('synthetic');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  const usage = page.getByRole('region', { name: 'Model usage' });
  await expect(usage).toContainText('Cache-read input: 2,440');
  await expect(usage).toContainText('Cache-creation input: 0');
  await expect(usage).toContainText('Cache-read input: Not reported');
  await expect(usage).toContainText(`Requested: ${model.requested_model}`);
  await expect(usage).toContainText(`Reported: ${model.reported_model}`);
  await expect(usage).toContainText('synthetic_usage');
});

test('inspector distinguishes GPT to Claude and Claude to Skynet prompts', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  const nonce = randomUUID();
  const task = `chain-task-${nonce}`;
  const work = `chain-work-${nonce}`;
  const claude = `chain-claude-${nonce}`;
  const qwen = `chain-qwen-${nonce}`;
  const content = (name: string, body: string, role = 'user', direction = 'out') => ({
    content_id: `${nonce}-${name}`, raw_base64: Buffer.from(body).toString('base64'),
    role, direction, content_type: 'text/plain', origin: 'imported', capture_layer: 'e2e',
  });
  const common = { schema_version: 1, producer_id: 'seed', producer_incarnation: nonce,
    occurred_at: new Date().toISOString(), origin: 'imported', observation_layer: 'e2e',
    capture_completeness: 'complete', task_id: task, work_item_id: work };
  const event = (sequence: number, event_type: string, fields: Record<string, unknown>) => ({
    ...common, event_id: `${nonce}-event-${sequence}`, producer_sequence: sequence,
    event_type, ...fields,
  });
  const ingested = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      event(1, 'task.created', { work_item_id: undefined, payload: {} }),
      event(2, 'work_item.created', { payload: { category: 'prompt-chain-check' } }),
      event(3, 'call.received', { call_id: claude, requested_model: 'sonnet-seed', payload: {} }),
      event(4, 'message.received', { call_id: claude, payload: {
        message_id: `${nonce}-gpt-in`, role: 'user', direction: 'in',
        original_content_id: `${nonce}-gpt-original`, effective_content_id: `${nonce}-gpt-original` } }),
      event(5, 'message.sent', { call_id: claude, payload: {
        message_id: `${nonce}-claude-out`, role: 'user', direction: 'out',
        original_content_id: `${nonce}-gpt-original`, effective_content_id: `${nonce}-claude-effective` } }),
      event(6, 'message.sent', { call_id: claude, payload: {
        message_id: `${nonce}-claude-system-message`, role: 'system', direction: 'out',
        original_content_id: `${nonce}-claude-system`, effective_content_id: `${nonce}-claude-system` } }),
      event(7, 'call.received', { call_id: qwen, requested_model: 'qwen-seed',
        payload: { parent_call_id: claude } }),
      event(8, 'message.sent', { call_id: qwen, payload: {
        message_id: `${nonce}-qwen-out`, role: 'user', direction: 'out',
        original_content_id: `${nonce}-qwen-original`, effective_content_id: `${nonce}-qwen-effective` } }),
      event(9, 'call.dispatched', { call_id: qwen, payload: { destination: 'skynet' } }),
    ], content: [
      content('gpt-original', 'GPT original question', 'user', 'in'),
      content('claude-effective', 'Claude effective question'),
      content('claude-system', 'Claude worker instruction', 'system'),
      content('qwen-original', 'Skynet original subtask'),
      content('qwen-effective', 'Skynet effective subtask'),
    ] },
  });
  expect(ingested.ok()).toBe(true);
  await page.getByLabel('Origin').selectOption('imported');
  await page.getByRole('button', { name: new RegExp(work) }).first().click();
  await expect(page.locator('.body-card').filter({ hasText: 'GPT → Claude original prompt' }))
    .toContainText('GPT original question');
  await expect(page.locator('.body-card').filter({ hasText: 'Prompt sent to Claude' }))
    .toContainText('Claude effective question');
  await expect(page.locator('.body-card').filter({ hasText: 'Claude → Skynet original prompt' }))
    .toContainText('Skynet original subtask');
  await expect(page.locator('.body-card').filter({ hasText: 'Effective prompt sent to Skynet' }))
    .toContainText('Skynet effective subtask');
  await page.getByText('Claude worker instructions').click();
  await expect(page.getByText('Claude worker instruction', { exact: true })).toBeVisible();
});
