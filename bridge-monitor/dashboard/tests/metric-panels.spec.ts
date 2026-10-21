import { expect, test } from '@playwright/test';

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
}

test('analysis panels start collapsed below work and toggle independently with the keyboard', async ({ page }) => {
  await openDashboard(page);
  const percentages = page.getByRole('region', { name: 'Percentage panel', exact: true });
  const router = page.getByRole('region', { name: 'Parent router shadow cohort' });
  const classifier = page.getByRole('region', { name: 'Gemma classifier shadow cohort' });
  for (const panel of [percentages, router, classifier]) {
    await expect(panel).toBeVisible();
    await expect(panel.locator('details')).not.toHaveAttribute('open');
    const work = await page.getByRole('region', { name: 'Recent and historical work' }).boundingBox();
    const box = await panel.boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(work!.y + work!.height);
  }
  const summary = percentages.locator('summary');
  await summary.focus();
  await summary.press('Enter');
  await expect(percentages.locator('details')).toHaveAttribute('open', '');
  await expect(router.locator('details')).not.toHaveAttribute('open');
  await summary.press(' ');
  await expect(percentages.locator('details')).not.toHaveAttribute('open');
});

test('donut charts stay visible above the live flow while percentage tiles collapse', async ({ page }) => {
  await openDashboard(page);
  const charts = page.getByRole('region', { name: 'Live monitor charts', exact: true });
  await expect(charts).toBeVisible();
  await expect(charts.getByRole('img')).toHaveCount(4);
  const chartBox = await charts.boundingBox();
  const flowBox = await page.getByRole('region', { name: 'Current orchestration flow' }).boundingBox();
  expect(chartBox!.y + chartBox!.height).toBeLessThanOrEqual(flowBox!.y);
  const panel = page.getByRole('region', { name: 'Percentage panel', exact: true });
  await expect(panel.locator('details')).not.toHaveAttribute('open');
  await expect(panel.getByRole('article')).toHaveCount(0);
  await panel.locator('summary').click();
  const metrics = await (await page.request.get('/api/v1/metrics?origin=synthetic')).json();
  for (const [title, value, unit] of [
    ['Routing', metrics.metrics.routed, 'work items'],
    ['Local contribution', metrics.metrics.contribution, 'delivered results'],
    ['Paid takeover', metrics.metrics.takeover, 'routed work items'],
    ['Disposition coverage', metrics.coverage.disposition, 'delivered results'],
  ] as const) {
    const chart = charts.getByRole('article', { name: `${title} chart`, exact: true });
    await expect(chart).toContainText(`${value.percent}%`);
    await expect(chart).toContainText(`${value.numerator} / ${value.denominator}`);
    await expect(chart).toContainText(`Unit: ${unit}`);
  }
  const button = panel.getByRole('button', { name: /Not routed/ });
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await panel.locator('summary').click();
  await expect(charts).toBeVisible();
  await expect(page.getByText('Loaded work list filtered by Not routed.', { exact: false })).toBeVisible();
  const reloaded = page.waitForResponse(r => r.url().includes('/api/v1/metrics?') && r.url().includes('consumer_stage=gpt'));
  await page.getByLabel('Consumer').selectOption('gpt');
  await reloaded;
  await expect(panel.locator('details')).not.toHaveAttribute('open');
  await panel.locator('summary').click();
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(panel.getByRole('button', { name: /GPT-reported acceptance/ })).toBeVisible();
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed', 'false');
});

test('donut charts distinguish unknown rates from a reported zero', async ({ page }) => {
  await page.route('**/api/v1/metrics?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.metrics.contribution = { numerator: 0, denominator: 0, percent: null };
    data.metrics.takeover = { numerator: 0, denominator: 40, percent: 0 };
    data.coverage.disposition = { numerator: 0, denominator: 0, percent: null };
    await route.fulfill({ json: data });
  });
  await openDashboard(page);
  const panel = page.getByRole('region', { name: 'Live monitor charts', exact: true });
  const unknown = panel.getByRole('article', { name: 'Local contribution chart' });
  await expect(unknown).toContainText('N/A');
  await expect(unknown.getByRole('img')).toHaveAccessibleName(/N\/A|not available/);
  const missing = panel.getByRole('article', { name: 'Disposition coverage chart' });
  await expect(missing).toContainText('N/A');
  const zero = panel.getByRole('article', { name: 'Paid takeover chart' });
  await expect(zero).toContainText('0%');
  await expect(zero).toContainText('0 / 40');
  await expect(zero.getByRole('img')).toHaveAccessibleName(/0%/);
});

test('donut charts fit a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDashboard(page);
  const panel = page.getByRole('region', { name: 'Live monitor charts', exact: true });
  const charts = panel.getByRole('article');
  for (const chart of await charts.all()) {
    await expect(chart).toBeVisible();
    const box = await chart.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  }
  const first = await charts.nth(0).boundingBox();
  const second = await charts.nth(1).boundingBox();
  expect(second!.y).toBeGreaterThan(first!.y);
});
