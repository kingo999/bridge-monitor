import { expect, test } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';

test('service metrics match the 100-item sample and history keeps topology current', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await expect(page.getByRole('button', { name: /Routed to Skynet/ })).toContainText('40%');
  await expect(page.getByRole('button', { name: /Not routed/ })).toContainText('60%');
  const acceptance = page.getByRole('button', { name: /Claude-reported acceptance/ });
  await expect(acceptance).toContainText('60%');
  await expect(acceptance).toContainText('Valid disposition records; not independently quality-reviewed');
  await expect(page.getByRole('button', { name: /Paid takeover/ })).toContainText('10%');
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByText('Current topology · grouped Claude workers')).toBeVisible();
  await page.getByRole('button', { name: /Not routed/ }).click();
  await expect(page.getByText('Work list filtered by Not routed')).toBeVisible();
});

test('GPT consumer view labels missing acknowledgements as unknown', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await page.getByLabel('Consumer').selectOption('gpt');

  const acceptance = page.getByRole('button', { name: /GPT-reported acceptance/ });
  await expect(acceptance).toContainText('No GPT acknowledgement captured; final use unknown');
  await acceptance.click();
  await expect(page.getByText('Work list filtered by GPT-reported acceptance')).toBeVisible();
});

test('GPT acknowledgement shows parent-submitted answer with capture boundary', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  const nonce = randomUUID();
  const task = `parent-task-${nonce}`;
  const work = `parent-work-${nonce}`;
  const parent = `gpt:${task}`;
  const claude = `parent-claude-${nonce}`;
  const paidResult = `parent-paid-result-${nonce}`;
  const parentResult = `parent-output-result-${nonce}`;
  const paidContent = `parent-paid-content-${nonce}`;
  const parentContent = `parent-output-content-${nonce}`;
  const sha = createHash('sha256').update('Claude gave 518').digest('hex');
  const common = { schema_version: 1, producer_id: 'seed', producer_incarnation: nonce,
    occurred_at: new Date().toISOString(), origin: 'imported', observation_layer: 'e2e',
    capture_completeness: 'complete', task_id: task, work_item_id: work };
  const event = (sequence: number, event_type: string, fields: Record<string, unknown>) => ({
    ...common, event_id: `${nonce}-event-${sequence}`, producer_sequence: sequence,
    event_type, ...fields,
  });
  const content = (content_id: string, body: string) => ({
    content_id, raw_base64: Buffer.from(body).toString('base64'), role: 'assistant',
    direction: 'in', content_type: 'text/plain', origin: 'imported', capture_layer: 'e2e',
  });
  const ingested = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      event(1, 'task.created', { work_item_id: undefined, payload: {} }),
      event(2, 'work_item.created', { payload: { category: 'parent-ack-check' } }),
      event(3, 'call.received', { call_id: parent, payload: { origin: 'codex_caller' } }),
      event(4, 'call.received', { call_id: claude, requested_model: 'sonnet-seed',
        payload: { parent_call_id: parent } }),
      event(5, 'result.delivered', { call_id: claude,
        payload: { result_id: paidResult, raw_content_id: paidContent,
          delivered_content_id: paidContent } }),
      event(6, 'result.delivered', { call_id: parent,
        payload: { result_id: parentResult, raw_content_id: parentContent,
          delivered_content_id: parentContent } }),
      event(7, 'disposition.recorded', { call_id: claude,
        payload: { disposition_id: `ack-${nonce}`, result_id: paidResult,
          actor_id: `gpt-parent:${parent}`, stage: 'gpt', judgment: 'accepted',
          input_sha256: sha, output_result_id: parentResult } }),
    ], content: [content(paidContent, 'Claude gave 518'),
      content(parentContent, 'GPT kept 518')] },
  });
  expect(ingested.ok()).toBe(true);
  await page.getByLabel('Origin').selectOption('imported');
  await page.getByLabel('Consumer').selectOption('gpt');
  await expect(page.getByRole('button', { name: /GPT-reported acceptance/ }))
    .toContainText('Parent-reported');
  await page.getByRole('button', { name: new RegExp(work) }).first().click();
  await expect(page.locator('.body-card').filter({ hasText: 'Parent-submitted answer' }))
    .toContainText('GPT kept 518');
  await expect(page.locator('.inspector-footer')).toContainText('GPT disposition: accepted');
  await expect(page.getByText('Final Codex answer not independently observed',
    { exact: true })).toBeVisible();
});

test('resolved work without a delegation record is visible separately from pending routing', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.locator('.connection')).toContainText('Connected');
  const unique = randomUUID();
  const taskId = `no-route-task-${unique}`;
  const workId = `no-route-work-${unique}`;
  const occurredAt = new Date().toISOString();
  const base = { schema_version: 1, producer_id: 'seed',
    producer_incarnation: `no-route-test-${unique}`, occurred_at: occurredAt,
    origin: 'observed', observation_layer: 'e2e', capture_completeness: 'complete' };
  const response = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      { ...base, event_id: `task-created-${unique}`, producer_sequence: 1,
        event_type: 'task.created', task_id: taskId, payload: {} },
      { ...base, event_id: `work-created-${unique}`, producer_sequence: 2,
        event_type: 'work_item.created', task_id: taskId, work_item_id: workId,
        payload: { category: 'no-route-check' } },
      { ...base, event_id: `work-resolved-${unique}`, producer_sequence: 3,
        event_type: 'work.resolved', task_id: taskId, work_item_id: workId,
        payload: { resolution_id: `resolution-${unique}`, outcome: 'paid_direct' } },
    ], content: [] },
  });
  expect(response.ok()).toBe(true);
  const pendingMetric = page.getByRole('button', { name: /Routing pending/ });
  await expect(pendingMetric).toContainText(/[1-9]\d* resolved without a delegation record/);
  await expect(page.locator('tr').filter({ hasText: workId })).toContainText('No route record');
});
