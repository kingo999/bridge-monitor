import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

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
