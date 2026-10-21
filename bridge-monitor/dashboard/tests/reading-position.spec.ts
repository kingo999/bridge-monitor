import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test('a delayed previous cohort cannot replace the current work list and metrics', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.locator('.connection')).toContainText('Connected');
  const workHeading = page.getByRole('region', { name: 'Recent and historical work' })
    .getByRole('heading', { name: /Recent & active work/ });
  const observedHeading = await workHeading.innerText();
  const routedCount = page.getByRole('button', { name: /Routed to Skynet/ }).locator('small');
  const observedRouted = await routedCount.innerText();

  let releaseSynthetic!: () => void;
  const syntheticGate = new Promise<void>(resolve => { releaseSynthetic = resolve; });
  let syntheticStarted!: () => void;
  const syntheticSeen = new Promise<void>(resolve => { syntheticStarted = resolve; });
  await page.route('**/api/v1/work-items?**', async route => {
    if (new URL(route.request().url()).searchParams.get('origin') !== 'synthetic') {
      await route.continue();
      return;
    }
    syntheticStarted();
    await syntheticGate;
    await route.continue();
  });

  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await syntheticSeen;
  const observedResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/work-items' &&
    new URL(response.url()).searchParams.get('origin') === 'observed');
  const observedMetrics = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/metrics' &&
    new URL(response.url()).searchParams.get('origin') === 'observed');
  await page.locator('.toolbar').getByLabel('Origin').selectOption('observed');
  await observedResponse;
  await observedMetrics;
  await page.waitForTimeout(200);
  await expect(workHeading).toHaveText(observedHeading);
  const syntheticResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/work-items' &&
    new URL(response.url()).searchParams.get('origin') === 'synthetic');
  releaseSynthetic();
  await syntheticResponse;
  await page.waitForTimeout(250);
  await expect(workHeading).toHaveText(observedHeading);
  await expect(routedCount).toHaveText(observedRouted);
});

test('a delayed load-more page cannot enter a different cohort', async ({ page, request }) => {
  const unique = randomUUID();
  const taskId = `load-more-task-${unique}`;
  const created = await request.post('/api/v1/events/batch', {
    headers: { Authorization: 'Bearer producer-secret' },
    data: { events: [
      { schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
        producer_sequence: 1, event_id: `load-more-task-event-${unique}`,
        event_type: 'task.created', occurred_at: new Date().toISOString(),
        origin: 'synthetic', observation_layer: 'e2e', capture_completeness: 'complete',
        task_id: taskId, payload: {} },
      { schema_version: 1, producer_id: 'seed', producer_incarnation: unique,
        producer_sequence: 2, event_id: `load-more-work-event-${unique}`,
        event_type: 'work_item.created', occurred_at: new Date().toISOString(),
        origin: 'synthetic', observation_layer: 'e2e', capture_completeness: 'complete',
        task_id: taskId, work_item_id: `load-more-work-${unique}`,
        payload: { category: `load-more-${unique}` } },
    ], content: [] },
  });
  expect(created.ok()).toBe(true);
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.locator('.connection')).toContainText('Connected');
  const workPanel = page.getByRole('region', { name: 'Recent and historical work' });
  const workHeading = workPanel.getByRole('heading', { name: /Recent & active work/ });
  const observedHeading = await workHeading.innerText();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await expect(workPanel.getByRole('heading', { name: /Recent & active work/ })).toContainText('(100 shown)');

  let releasePage!: () => void;
  const pageGate = new Promise<void>(resolve => { releasePage = resolve; });
  let pageStarted!: () => void;
  const pageSeen = new Promise<void>(resolve => { pageStarted = resolve; });
  await page.route('**/api/v1/work-items?**', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('origin') === 'synthetic' && url.searchParams.get('offset') === '100') {
      pageStarted();
      await pageGate;
    }
    await route.continue();
  });
  await workPanel.getByRole('button', { name: 'Load more' }).click();
  await pageSeen;
  const observedResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/work-items' &&
    new URL(response.url()).searchParams.get('origin') === 'observed');
  const observedMetrics = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/metrics' &&
    new URL(response.url()).searchParams.get('origin') === 'observed');
  await page.locator('.toolbar').getByLabel('Origin').selectOption('observed');
  await observedResponse;
  await observedMetrics;
  await page.waitForTimeout(200);
  await expect(workHeading).toHaveText(observedHeading);
  const syntheticResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/v1/work-items' &&
    new URL(response.url()).searchParams.get('offset') === '100');
  releasePage();
  const delayedResponse = await syntheticResponse;
  expect((await delayedResponse.json()).length).toBe(1);
  await page.waitForTimeout(250);
  await expect(workHeading).toHaveText(observedHeading);
});

test('selected exchange remains selected when the list refreshes', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  await expect(page.getByText('demo-work-061').last()).toBeVisible();
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByText('Qwen original')).toBeVisible();
});

test('live refresh keeps older work items loaded for historical reading', async ({ page, request }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.locator('.connection')).toContainText('Connected');
  const nonce = randomUUID();
  const base = { schema_version: 1, producer_id: 'seed', producer_incarnation: nonce,
    occurred_at: new Date().toISOString(), origin: 'synthetic', observation_layer: 'e2e',
    capture_completeness: 'complete' };
  const addWork = async (sequence: number, label: string) => {
    const taskId = `reading-task-${nonce}-${sequence}`;
    const workId = `reading-work-${nonce}-${sequence}`;
    const result = await request.post('/api/v1/events/batch', {
      headers: { Authorization: 'Bearer producer-secret' },
      data: { events: [
        { ...base, event_id: `${nonce}-${sequence}`, producer_sequence: sequence,
          event_type: 'task.created', task_id: taskId, payload: {} },
        { ...base, event_id: `${nonce}-${sequence + 1}`, producer_sequence: sequence + 1,
          event_type: 'work_item.created', task_id: taskId, work_item_id: workId,
          payload: { category: label } },
      ], content: [] },
    });
    expect(result.ok()).toBe(true);
  };

  await addWork(1, 'reading-first');
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  const workPanel = page.getByRole('region', { name: 'Recent and historical work' });
  const workHeading = workPanel.getByRole('heading', { name: /Recent & active work/ });
  await expect(workHeading).toContainText('(100 shown)');
  await workPanel.getByRole('button', { name: 'Load more' }).click();
  await expect.poll(async () => Number((await workHeading.innerText()).match(/\((\d+) shown\)/)?.[1] || 0))
    .toBeGreaterThan(100);
  const loadedCount = Number((await workHeading.innerText()).match(/\((\d+) shown\)/)?.[1]);
  await expect(workPanel.getByRole('button', { name: /demo-work-001/ })).toBeVisible();
  await expect(page.locator('.connection')).toContainText('Connected');
  await page.waitForTimeout(300);
  const historyLimits: number[] = [];
  page.on('request', ongoing => {
    const url = new URL(ongoing.url());
    if (url.pathname === '/api/v1/work-items' && url.searchParams.get('origin') === 'synthetic') {
      historyLimits.push(Number(url.searchParams.get('limit')));
    }
  });
  await addWork(3, 'reading-second');
  await expect(workPanel.getByText('reading-second')).toBeVisible();
  await expect(workHeading).toContainText(`(${loadedCount + 1} shown)`);
  await expect(workPanel.getByRole('button', { name: /demo-work-001/ })).toBeVisible();
  expect(historyLimits.some(limit => limit > 100 && limit <= 1000)).toBe(true);
});
