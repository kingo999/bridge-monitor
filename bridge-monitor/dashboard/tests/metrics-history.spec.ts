import { expect, test } from '@playwright/test';

test('service metrics match the 100-item sample and history keeps topology current', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await expect(page.getByRole('button', { name: /Routed to Skynet/ })).toContainText('40%');
  await expect(page.getByRole('button', { name: /Not routed/ })).toContainText('60%');
  await expect(page.getByRole('button', { name: /Confirmed acceptance/ })).toContainText('60%');
  await expect(page.getByRole('button', { name: /Paid takeover/ })).toContainText('10%');
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByText('Current topology · grouped Claude workers')).toBeVisible();
  await page.getByRole('button', { name: /Not routed/ }).click();
  await expect(page.getByText('Work list filtered by Not routed')).toBeVisible();
});
