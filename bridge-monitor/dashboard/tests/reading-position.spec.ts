import { expect, test } from '@playwright/test';

test('selected exchange remains selected when the list refreshes', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  await expect(page.getByText('demo-work-061').last()).toBeVisible();
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByText('Qwen original')).toBeVisible();
});
