import { expect, test } from '@playwright/test';

test('dashboard offers a native Design workflows tab with shared tab navigation', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  const tab = page.getByRole('tab', { name: 'Design workflows', exact: true });
  await tab.click();
  await expect(tab).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#view-design')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Design workflows', exact: true })).toBeVisible();
  await expect(page.getByLabel('Design project')).toBeVisible();
  await tab.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Audit', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Codex / Claude', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Design workflows (read-only)' })).toHaveCount(0);
});

test('native design tab filters workflows and refreshes selected evidence safely', async ({ page }) => {
  let delivered = true;
  const base = { owner_id: 'owner', request_key: 'design-feature', current_iteration: 2, version: 3,
    status_actor: 'chat', status_reason: null, updated_at: '2026-11-04T10:00:00Z',
    accepted_count: 0, rejected_count: 0, revision_count: 1 };
  const workflow = { ...base, workflow_id: 'design-example', repository_id: 'demo', project_root: 'C:/demo',
    status: 'delivered', outcome_label: 'Delivered to Chat on iteration 2' };
  const queries: string[] = [];
  await page.route('**/api/v1/design-mode/workflows**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/design-example')) return route.fulfill({ json: { ...workflow,
      iterations: [], evidence: [], audit: [], deliveries: [],
      hops: [{ iteration: 2, sequence: 1, from_actor: 'codex', to_actor: 'claude', state: 'completed' }],
      artifacts: [{ iteration: 2, kind: 'review', sha256: 'a'.repeat(64), bytes: 128, created_at: '2026-11-04T10:00:00Z' }] } });
    queries.push(url.search);
    return route.fulfill({ json: { available: true, decision_filter_configured: true,
      projects: [{ repository_id: 'demo', project_root: 'C:/demo' }],
      workflows: url.searchParams.has('awaiting_my_decision') && !delivered ? [] : [workflow] } });
  });
  await page.goto('/?view=design');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Design workflows', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByLabel('Design project').selectOption('demo');
  await page.getByLabel('Awaiting my decision', { exact: true }).check();
  await expect.poll(() => queries.some(q => q.includes('repository_id=demo&awaiting_my_decision=true'))).toBeTruthy();
  await page.getByRole('button', { name: 'design-example', exact: true }).click();
  const evidence = page.getByRole('region', { name: 'Design workflow evidence' });
  await expect(evidence).toContainText('codex');
  await expect(evidence).toContainText('claude');
  await expect(evidence).toContainText('a'.repeat(64));
  await page.screenshot({ path: '../var/audit-ui-work/design-tab.png', fullPage: true });
  await page.getByRole('tab', { name: 'Audit', exact: true }).click();
  await page.getByRole('tab', { name: 'Design workflows', exact: true }).click();
  await expect(page.getByLabel('Design project')).toHaveValue('demo');
  delivered = false;
  await page.getByRole('button', { name: 'Refresh workflows' }).click();
  await expect(page.getByText('No workflows awaiting your decision', { exact: true })).toBeVisible();
  await expect(evidence).toHaveCount(0);
});
