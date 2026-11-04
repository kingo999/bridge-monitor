import { expect, test } from '@playwright/test';

test('Codex Claude includes live flow and hides unavailable service nodes and connectors', async ({ page }) => {
  expect((await page.request.get('/api/v1/services')).status()).toBe(401);
  let localAvailable = false;
  await page.route('**/api/v1/services', route => route.fulfill({ json: {
    checked_at: new Date().toISOString(), services: { claude: true, qwen: localAvailable, gemma: localAvailable },
  } }));
  await page.route('**/api/v1/classifier/activity', route => route.fulfill({ json: {
    available: true, checked_at: new Date().toISOString(), queued: 0, classifying: 0, uncertain: 0, latest: null,
  } }));
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard', exact: true }).click();
  const flow = page.getByRole('region', { name: 'Current orchestration flow' });
  await expect(flow).toBeVisible();
  await expect(flow).toHaveCount(1);
  const statsBottom = await page.locator('.cc-stats').evaluate(e => e.getBoundingClientRect().bottom);
  const flowTop = await flow.evaluate(e => e.getBoundingClientRect().top);
  const networkTop = await page.getByRole('button', { name: 'Network audit', exact: true }).evaluate(e => e.getBoundingClientRect().top);
  expect(flowTop).toBeGreaterThanOrEqual(statsBottom);
  expect(flowTop).toBeLessThan(networkTop);
  await expect(flow.getByText('Skynet / Qwen', { exact: true })).toHaveCount(0);
  await expect(flow.locator('[data-id="gemma"]')).toHaveCount(0);
  await expect(flow.locator('[data-id="claude-qwen"],[data-id="gpt-gemma"]')).toHaveCount(0);
  await expect(flow.locator('[data-id="gpt-claude"]')).toBeVisible();
  localAvailable = true;
  await page.getByRole('tab', { name: 'Experiments', exact: true }).click();
  await expect(flow.getByText('Skynet / Qwen', { exact: true })).toBeVisible();
  await expect(flow.locator('[data-id="gemma"]')).toBeVisible();
  await expect(flow.locator('[data-id="claude-qwen"],[data-id="gpt-gemma"]')).toHaveCount(2);
});


test('ChatGPT tile opens a separate handoff audit', async ({ page }) => {
  await page.route('**/api/v1/audit/design-handoffs*', route => route.fulfill({ json: {
    available: true, total: 1, next: null, rows: [{ id: '123', workflow_id: 'dm-example',
      iteration: 2, time: '2026-11-04T08:00:00Z', action: 'revise' }],
  } }));
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard', exact: true }).click();
  const tile = page.locator('[data-id="chatgpt"]');
  await expect(tile).toContainText('1 handoff');
  await expect(page.locator('[data-id="chatgpt-gpt"]')).toBeVisible();
  await tile.getByRole('button').click();
  await expect(page.getByRole('button', { name: 'Design Mode handoffs', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('row').filter({ hasText: 'dm-example' })).toContainText('Revised brief');
  await expect(page.getByRole('textbox', { name: 'Search audit', exact: true })).toHaveCount(0);
});


test('core tiles stay visible with grey idle links when Claude is unavailable', async ({ page }) => {
  await page.route('**/api/v1/services', route => route.fulfill({ json: {
    checked_at: new Date().toISOString(), services: { claude: false, qwen: true, gemma: true },
  } }));
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard', exact: true }).click();
  const flow = page.getByRole('region', { name: 'Current orchestration flow' });
  await expect(flow.locator('[data-id="qwen"]')).toBeVisible();
  await expect(flow.locator('[data-id="chatgpt"],[data-id="gpt"],[data-id="claude"]')).toHaveCount(3);
  await expect(flow.locator('[data-id="claude"]')).toContainText('Activity unavailable');
  for (const id of ['chatgpt-gpt', 'gpt-claude', 'claude-qwen']) {
    await expect(flow.locator(`[data-id="${id}"] .react-flow__edge-path`)).toHaveCSS('stroke', 'rgb(100, 116, 139)');
    await expect(flow.locator(`[data-id="${id}"]`)).not.toHaveClass(/animated/);
  }
  await expect(flow.locator('[data-id="gpt-gemma"]')).toBeVisible();
});


test('pending background readiness is polled promptly without waiting ten seconds', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/v1/services', route => {
    calls++;
    return route.fulfill({ json: { refreshing: calls === 1, checked_at: new Date().toISOString(),
      services: { claude: calls > 1, qwen: false, gemma: false } } });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard', exact: true }).click();
  const flow = page.getByRole('region', { name: 'Current orchestration flow' });
  await expect(flow.locator('[data-id="claude"] .node-status')).toHaveAttribute('data-state', /^(working|waiting|idle|uncertain)$/, { timeout: 4000 });
  expect(calls).toBeGreaterThan(1);
  await expect(flow.locator('[data-id="qwen"],[data-id="gemma"]')).toHaveCount(0);
});
