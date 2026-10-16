import { expect, test } from '@playwright/test';

test('synthetic exchange has grouped flow and side-by-side response comparison', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await expect(page.getByRole('button', { name: /Claude workers/ })).toBeVisible();
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  await expect(page.getByText('Qwen original')).toBeVisible();
  await expect(page.getByText('What is 2 + 2?').first()).toBeVisible();
  await expect(page.getByRole('tab', { name: 'decision' })).toBeVisible();
  await page.getByRole('tab', { name: 'timeline' }).click();
  await expect(page.getByText('call.dispatched')).toBeVisible();
  await expect(page.locator('.react-flow__node').first()).toBeVisible();
  await page.screenshot({ path: '../var/reports/dashboard/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 740, height: 1050 });
  await expect(page.getByRole('heading', { name: 'Live orchestration flow' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Selected work item' })).toBeVisible();
  await expect(page.locator('.react-flow__node').first()).toBeVisible();
  await page.screenshot({ path: '../var/reports/dashboard/narrow.png', fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
