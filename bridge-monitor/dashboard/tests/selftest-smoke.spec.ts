import { expect, test } from '@playwright/test';

test('isolated self-test server protects content and opens the current dashboard', async ({ page, request }) => {
  expect((await request.get('/api/v1/content/demo-prompt-061')).status()).toBe(401);
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.getByRole('heading', { name: 'Codex / Claude overview' })).toBeVisible();
  const cookies = await page.context().cookies();
  expect(cookies.find(cookie => cookie.name === 'bridge_monitor_session')?.httpOnly).toBe(true);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByLabel('Operator credential')).toBeVisible();
});
