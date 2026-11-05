import { expect, test } from '@playwright/test';

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'ignoreErrors' }); });

test('Codex Claude includes live flow and hides unavailable service nodes and connectors', async ({ page }) => {
  expect((await page.request.get('/api/v1/services')).status()).toBe(401);
  let localAvailable = false;
  await page.route('**/api/v1/services', route => route.fulfill({ json: {
    checked_at: new Date().toISOString(), services: { claude: true, qwen: localAvailable, gemma: localAvailable },
    model_settings: {
      claude: [{ model: 'claude-haiku-test', effort: 'low' }, { model: 'claude-sonnet-test', effort: 'medium' },
        { model: 'claude-opus-test', effort: 'medium' }],
      qwen: [{ model: 'qwen-test:32b', effort: null }],
      gemma: [{ model: 'gemma-test:12b', effort: 'Thinking off' }],
    },
    codex_models: [{ model: 'gpt-codex-test', effort: 'high', effort_source: 'last_configured' }],
  } }));
  await page.route('**/api/v1/audit/design-handoffs?limit=1', route => route.fulfill({ json: {
    available: true, total: 1, next: null, rows: [],
    chat_selection: { model: 'GPT Chat Test', effort: 'Extra High', time: new Date().toISOString() },
  } }));
  await page.route('**/api/v1/classifier/activity', route => route.fulfill({ json: {
    available: true, checked_at: new Date().toISOString(), queued: 0, classifying: 0, uncertain: 0, latest: null,
  } }));
  await page.route('**/api/v1/snapshot?*', async route => {
    const response = await route.fetch();
    const snapshot = await response.json();
    snapshot.status = { ...snapshot.status, active: 0, queued: 0 };
    snapshot.capacity = null;
    snapshot.worker_executions = [];
    await route.fulfill({ response, json: snapshot });
  });
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
  await expect(flow.locator('[data-id="chatgpt"]')).toContainText('Last handoff · GPT Chat Test · Extra High effort');
  await expect(flow.locator('[data-id="gpt"]')).toContainText('gpt-codex-test · high effort · Last configured');
  await expect(flow.locator('[data-id="qwen"]')).toContainText('Configured · qwen-test:32b · Effort unavailable');
  await expect(flow.locator('[data-id="gemma"]')).toContainText('Configured · gemma-test:12b · Thinking off');
  const claude = flow.locator('.react-flow__node[data-id="claude"]');
  const gemma = flow.locator('.react-flow__node[data-id="gemma"]');
  await expect(claude.locator('.flow-model-details')).toHaveCount(0);
  const claudeBox = (await claude.boundingBox())!;
  const gemmaBox = (await gemma.boundingBox())!;
  expect(claudeBox.y + claudeBox.height).toBeLessThan(gemmaBox.y);
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
