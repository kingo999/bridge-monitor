// Opt-in live browser observer. Start before the isolated Codex pilot call.
import { writeFileSync } from 'node:fs';
import { chromium } from '@playwright/test';

const baseURL = process.env.BRIDGE_MONITOR_PILOT_URL;
const token = process.env.BRIDGE_MONITOR_PILOT_OPERATOR_TOKEN;
if (!baseURL || !token) throw new Error('pilot URL and operator token are required');
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1512, height: 950 } });
  await page.goto(baseURL);
  await page.getByLabel('Operator credential').fill(token);
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.connection.live').waitFor({ timeout: 30_000 });
  const baseline = new Set(await page.locator('.work-table tbody tr button small').allTextContents());
  const before = await page.evaluate(async () => (await fetch('/api/v1/snapshot?origin=observed')).json());
  const ready = { baseline: [...baseline], cursor: before.cursor.position, at: new Date().toISOString() };
  writeFileSync('../var/reports/dashboard/live-watch.ready.json', JSON.stringify(ready, null, 2));
  console.log(JSON.stringify({ phase: 'ready', ...ready }));
  let workId = '';
  const deadline = Date.now() + 180_000;
  while (!workId && Date.now() < deadline) {
    const ids = await page.locator('.work-table tbody tr button small').allTextContents();
    workId = ids.find(value => !baseline.has(value)) || '';
    if (!workId) await page.waitForTimeout(400);
  }
  if (!workId) throw new Error('no new observed work item appeared in the already-connected browser');
  await page.getByRole('button', { name: new RegExp(workId) }).first().click();
  await page.getByText(workId).last().waitFor();
  await page.screenshot({ path: '../var/reports/dashboard/live-inflight.png', fullPage: true });
  console.log(JSON.stringify({ phase: 'observed', workId, at: new Date().toISOString() }));
  writeFileSync('../var/reports/dashboard/live-watch.observed.json', JSON.stringify({ workId, at: new Date().toISOString() }, null, 2));
  const finalDeadline = Date.now() + 540_000;
  let row = '';
  while (Date.now() < finalDeadline) {
    row = await page.locator('.work-table tbody tr').filter({ hasText: workId }).innerText();
    if (/local_retained|paid_replaced|paid_direct|paid_revision_with_contribution/.test(row)) break;
    await page.waitForTimeout(1000);
  }
  await page.screenshot({ path: '../var/reports/dashboard/live-final.png', fullPage: true });
  const result = { phase: 'final', workId, row, at: new Date().toISOString() };
  writeFileSync('../var/reports/dashboard/live-watch.final.json', JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  if (!/local_retained|paid_replaced|paid_direct|paid_revision_with_contribution/.test(row)) {
    throw new Error('new observed work item did not reach a confirmed resolution');
  }
} finally {
  await browser.close();
}
