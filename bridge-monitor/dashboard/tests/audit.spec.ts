import { test, expect } from '@playwright/test';

test('audit endpoints require an operator', async ({ request }) => {
  expect((await request.get('/api/v1/audit/status')).status()).toBe(401);
  expect((await request.post('/api/v1/audit/search', { data: { view: 'network' } })).status()).toBe(401);
});

test('audit searches both retained histories and preserves overview cohort', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard', exact: true }).click();
  await expect(page.getByRole('button', { name: 'View audit', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View audit', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Audit', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Captured overview cohort', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Search all workflows' }).click();
  await page.getByLabel('Search audit', { exact: true }).filter({ visible: true }).fill('test');
  await page.getByRole('button', { name: 'Apply search', exact: true }).filter({ visible: true }).click();
  await expect(page.getByLabel('Search range').filter({ visible: true })).toHaveValue('all');
  await page.getByRole('button', { name: 'Network activity', exact: true }).click();
  await expect(page.getByText('Last health signal processed', { exact: false }).filter({ visible: true })).toBeVisible();
  await page.getByLabel('Search audit', { exact: true }).filter({ visible: true }).fill('192.0.2.8');
  await page.getByRole('button', { name: 'Apply search', exact: true }).filter({ visible: true }).click();
  await expect(page.getByLabel('Search range').filter({ visible: true })).toHaveValue('all');
  await expect(page.getByRole('alert').filter({ hasText: /\S/ })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Audit', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Experiments', exact: true })).toHaveAttribute('aria-selected', 'true');
});
