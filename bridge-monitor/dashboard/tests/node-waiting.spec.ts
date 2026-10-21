import { expect, test, type Page } from '@playwright/test';

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'ignoreErrors' }); });

async function openFlow(page: Page, { active = 0, queued = 0, uncertain = 0, available = true, waiting = 0 } = {}) {
  await page.route('**/api/v1/snapshot?*', async route => {
    const response = await route.fetch();
    const snapshot = await response.json();
    snapshot.status = available ? { ...snapshot.status, active, queued, held_uncertain: uncertain } : null;
    snapshot.capacity = available ? {
      claude: { active, queued, held_uncertain: uncertain },
      qwen: { active, queued, held_uncertain: uncertain },
    } : null;
    snapshot.worker_executions = Array.from({ length: active }, (_, i) => ({
      call_id: `worker-${i}`, work_item_id: null, worker_instance_id: `worker-${i}`,
      requested_model: 'sonnet', reported_model: null, occurred_at: new Date().toISOString(),
      waiting_on_local: i < waiting,
    }));
    await route.fulfill({ response, json: snapshot });
  });
  await page.route('**/api/v1/classifier/activity', route => route.fulfill(available ? {
    json: { available: true, checked_at: new Date().toISOString(), queued,
      classifying: active, uncertain, latest: null },
  } : { status: 503, json: { detail: 'unavailable' } }));
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  return page.locator('.origin-node, .worker-group, .skynet-node, .gemma-activity');
}

test('queued nodes and the delegating parent show waiting, distinct from idle and working', async ({ page }) => {
  const nodes = await openFlow(page, { queued: 2 });
  await expect(nodes).toHaveCount(4);
  for (const node of await nodes.all()) {
    await expect(node).toHaveClass(/is-waiting/);
    await expect(node.locator('.node-status')).toHaveAttribute('data-state', 'waiting');
    await expect(node).toHaveCSS('outline-style', 'dashed');
    await expect(node).toHaveCSS('animation-name', 'none');
    const ring = node.locator('.node-orb, .worker-glyph');
    await expect.poll(() => ring.evaluate(el => getComputedStyle(el, '::after').animationName))
      .toBe('waiting-orbit');
  }
  await expect(page.locator('.origin-node')).toContainText('Waiting for bridge workers');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => page.locator('.origin-node .node-orb')
    .evaluate(el => getComputedStyle(el, '::after').animationName)).toBe('none');
});

test('active models keep their glow while GPT waits and queued work remains labelled', async ({ page }) => {
  const nodes = await openFlow(page, { active: 1, queued: 2 });
  await expect(page.locator('.origin-node')).toHaveClass(/is-waiting/);
  for (const selector of ['.worker-group', '.skynet-node', '.gemma-activity']) {
    const node = page.locator(selector);
    await expect(node).toHaveClass(/is-working/);
    await expect(node.locator('.node-status')).toHaveAttribute('data-state', 'working');
    await expect(node.locator('.node-queue')).toHaveText('2 queued');
    await expect(node).toHaveCSS('animation-name', 'model-node-glow');
  }
  await page.getByRole('button', { name: 'Pause display' }).click();
  for (const node of await nodes.all()) {
    await expect(node).not.toHaveClass(/is-working|is-waiting/);
    await expect(node.locator('.node-status')).toHaveAttribute('data-state', 'paused');
  }
  await page.getByRole('button', { name: 'Resume display' }).click();
  await expect(page.locator('.origin-node')).toHaveClass(/is-waiting/);
});

test('idle, uncertain and unavailable activity are never presented as waiting or working', async ({ page }) => {
  const nodes = await openFlow(page);
  for (const node of await nodes.all()) {
    await expect(node.locator('.node-status')).toHaveAttribute('data-state', 'idle');
    await expect(node).not.toHaveClass(/is-working|is-waiting/);
  }
});

test('held uncertainty remains visible and does not make GPT look like it is awaiting a result', async ({ page }) => {
  await openFlow(page, { uncertain: 1 });
  await expect(page.locator('.origin-node .node-status')).toHaveAttribute('data-state', 'idle');
  for (const selector of ['.worker-group', '.skynet-node', '.gemma-activity']) {
    await expect(page.locator(selector)).toHaveClass(/is-uncertain/);
    await expect(page.locator(selector+' .node-status')).toHaveAttribute('data-state', 'uncertain');
  }
});

test('unknown activity has no idle or waiting claim', async ({ page }) => {
  const nodes = await openFlow(page, { available: false });
  for (const node of await nodes.all()) {
    await expect(node.locator('.node-status')).toHaveAttribute('data-state', 'unavailable');
    await expect(node).not.toHaveClass(/is-working|is-waiting/);
  }
});

test('Claude awaiting its local tool shows waiting while Qwen works', async ({ page }) => {
  await openFlow(page, { active: 1, waiting: 1 });
  await expect(page.locator('.worker-group')).toHaveClass(/is-waiting/);
  await expect(page.locator('.worker-group .node-status')).toContainText('Waiting for Skynet');
  await expect(page.locator('.worker-execution')).toContainText('waiting for Skynet');
  await expect(page.locator('.worker-group')).toHaveCSS('animation-name', 'none');
  await expect(page.locator('.skynet-node')).toHaveClass(/is-working/);
});
