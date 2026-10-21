import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('selected Qwen call updates its preview without moving a reader through long output', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();

  const nonce = randomUUID();
  const task = `preview-task-${nonce}`;
  const work = `preview-work-${nonce}`;
  const call = `preview-call-${nonce}`;
  const message = `preview-message-${nonce}`;
  const common = { schema_version: 1, producer_id: 'seed', producer_incarnation: nonce,
    occurred_at: new Date().toISOString(), origin: 'imported', observation_layer: 'e2e',
    capture_completeness: 'complete', task_id: task, work_item_id: work };
  const event = (sequence: number, event_type: string, fields: Record<string, unknown>) => ({
    ...common, event_id: `${nonce}-event-${sequence}`, producer_sequence: sequence,
    event_type, ...fields,
  });
  const chunk = (sequence: number, body: string) => ({
    content_id: `${nonce}-chunk-${sequence}`, raw_base64: Buffer.from(body).toString('base64'),
    role: 'assistant', direction: 'in', content_type: 'text/plain', origin: 'imported',
    capture_layer: 'e2e', message_id: message,
  });
  const delta = (sequence: number) => event(sequence + 4, 'response.delta', {
    call_id: call, payload: { message_id: message, chunk_sequence: sequence,
      content_id: `${nonce}-chunk-${sequence}` },
  });
  const first = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      event(1, 'task.created', { work_item_id: undefined, payload: {} }),
      event(2, 'work_item.created', { payload: { category: 'live-output-check' } }),
      event(3, 'call.received', { call_id: call, requested_model: 'qwen-seed', payload: {} }),
      event(4, 'call.dispatched', { call_id: call, payload: { destination: 'skynet' } }),
      delta(1), delta(2),
    ], content: [chunk(1, 'Alpha '), chunk(2, 'β ')] },
  });
  expect(first.ok()).toBe(true);

  await page.locator('.toolbar').getByLabel('Origin').selectOption('imported');
  await page.getByRole('button', { name: new RegExp(work) }).first().click();
  await expect(page.locator('.body-card').filter({ hasText: 'Qwen live output' }))
    .toContainText('Alpha β');

  const next = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [delta(3)], content: [chunk(3, 'Gamma')] },
  });
  expect(next.ok()).toBe(true);
  await expect(page.locator('.body-card').filter({ hasText: 'Qwen live output' }))
    .toContainText('Alpha β Gamma');

  const longOutput = Array.from({ length: 100 }, (_, index) => `Line ${index + 1}: captured text`).join('\n');
  const longer = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [delta(4)], content: [{ ...chunk(4, `\n${longOutput}`) }] },
  });
  expect(longer.ok()).toBe(true);
  const preview = page.locator('.body-card').filter({ hasText: 'Qwen live output' });
  await expect(preview).toContainText('Line 100: captured text');
  await preview.getByRole('button', { name: 'Expand' }).click();
  const output = preview.locator('pre');
  const before = await output.evaluate(element => {
    element.scrollTop = 160;
    return element.scrollTop;
  });
  expect(before).toBeGreaterThan(0);
  const latest = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [delta(5)], content: [chunk(5, '\nNew tail')] },
  });
  expect(latest.ok()).toBe(true);
  await expect(output).toContainText('New tail');
  expect(await output.evaluate(element => element.scrollTop)).toBe(before);
});
