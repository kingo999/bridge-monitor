import { expect, test } from '@playwright/test';

test('operator session protects exchange bodies and can sign out', async ({ page, request }) => {
  expect((await request.get('/api/v1/content/demo-prompt-061')).status()).toBe(401);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Bridge Monitor' })).toBeVisible();
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.getByRole('heading', { name: 'Live orchestration flow' })).toBeVisible();
  const cookies = await page.context().cookies();
  expect(cookies.find(cookie => cookie.name === 'bridge_monitor_session')?.httpOnly).toBe(true);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByLabel('Operator credential')).toBeVisible();
});
