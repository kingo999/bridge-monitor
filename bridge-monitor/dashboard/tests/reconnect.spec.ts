import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

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
