import { expect, test } from '@playwright/test';

test('classifier updates without filter changes, pauses, resumes and clears stale activity on failure', async ({ page }) => {
  let completed = 0;
  let active = true;
  let unavailable = false;
  let completedAt = new Date().toISOString();
  await page.route('**/api/v1/classifier/cohort?*', async route => {
    const response = await route.fetch();
    const report = await response.json();
    report.counts.submitted = completed;
    report.counts.completed = completed;
    await route.fulfill({ response, json: report });
  });
  await page.route('**/api/v1/classifier/activity', route => route.fulfill(unavailable ? {
    status: 503, json: { detail: 'unavailable' },
  } : { json: {
    available: true, checked_at: new Date().toISOString(), queued: active ? 2 : 0,
    classifying: active ? 1 : 0, uncertain: 0,
    latest: { observation_id: 'live-observation', work_item_id: 'live-work',
      state: active ? 'dispatching' : 'completed', requested_model: 'gemma4:12b',
      captured_at: new Date().toISOString(), dispatched_at: new Date().toISOString(),
      completed_at: active ? null : completedAt, rec_route: active ? null : 'paid',
      origin_kind: 'controlled', capture_method: 'route_work' },
  } }));
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  const panel = page.getByRole('region', { name: 'Gemma classifier shadow cohort' });
  await panel.locator('summary').click();
  const submitted = panel.locator('.shadow-stats div').filter({ hasText: 'Submitted' }).locator('strong');
  await expect(submitted).toHaveText('0');
  completed = 1;
  await expect(submitted).toHaveText('1', { timeout: 6000 });
  const node = page.getByRole('region', { name: 'Gemma advisory activity' });
  await expect(node).toContainText('1 classifying');
  await expect(node).toContainText('2 queued');
  await expect(node).toContainText('Advisory only');
  await expect(page.locator('.react-flow__edge[data-id="gpt-gemma"]')).toHaveClass(/animated/);
  await expect(node.locator('.node-orb')).toHaveCSS('animation-name', 'model-working');
  await expect(node).toHaveCSS('animation-name', 'model-node-glow');
  const path = page.locator('.react-flow__edge[data-id="gpt-gemma"] .react-flow__edge-path');
  await expect(path).toHaveCSS('animation-name', 'traffic-flow');
  const firstOffset = await path.evaluate(el => getComputedStyle(el).strokeDashoffset);
  await expect.poll(() => path.evaluate(el => getComputedStyle(el).strokeDashoffset)).not.toBe(firstOffset);
  await page.getByLabel('Classifier capture method').selectOption('bridge_ingress');
  await page.getByLabel('Classifier origin', { exact: true }).selectOption('controlled');
  await page.getByRole('button', { name: 'Pause display' }).click();
  await expect(node).toContainText('Display paused');
  completed = 3;
  active = false;
  completedAt = new Date().toISOString();
  await page.waitForTimeout(2500);
  await expect(submitted).toHaveText('1');
  await page.getByRole('button', { name: 'Resume display' }).click();
  await expect(submitted).toHaveText('3');
  await expect(node).toContainText('0 classifying');
  await expect(node).toContainText('completed');
  await expect(page.locator('.react-flow__edge[data-id="gpt-gemma"]')).toHaveClass(/animated/);
  await expect(page.getByText('Result · recently completed')).toBeVisible();
  await expect(node.locator('.node-orb')).toHaveCSS('animation-name', 'none');
  await expect(node).toHaveCSS('animation-name', 'none');
  completedAt = new Date(Date.now() - 15000).toISOString();
  await expect(page.locator('.react-flow__edge[data-id="gpt-gemma"]')).not.toHaveClass(/animated/);
  await expect(page.getByLabel('Classifier capture method')).toHaveValue('bridge_ingress');
  await expect(page.getByLabel('Classifier origin', { exact: true })).toHaveValue('controlled');
  unavailable = true;
  await expect(node).toContainText('Activity unavailable', { timeout: 6000 });
  await expect(node).not.toContainText('classifying');
  await expect(page.getByRole('button', { name: /Claude workers/ })).toBeVisible();
});

test('Qwen path does not animate for Claude-only work', async ({ page }) => {
  await page.route('**/api/v1/snapshot?*', async route => {
    const response = await route.fetch();
    const snapshot = await response.json();
    snapshot.capacity = { claude: { active: 1, queued: 0, held_uncertain: 0 },
      qwen: { active: 0, queued: 0, held_uncertain: 0 } };
    await route.fulfill({ response, json: snapshot });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.locator('.react-flow__edge[data-id="gpt-claude"]')).toHaveClass(/animated/);
  await expect(page.locator('.react-flow__edge[data-id="claude-qwen"]')).not.toHaveClass(/animated/);
  await expect(page.locator('.worker-glyph')).toHaveCSS('animation-name', 'model-working');
  await expect(page.locator('.node-orb.qwen')).toHaveCSS('animation-name', 'none');
});
