// Opt-in, read-only browser check of a recorded Milestone 2 exchange.
import { chromium } from '@playwright/test';

const baseURL = process.env.BRIDGE_MONITOR_PILOT_URL;
const token = process.env.BRIDGE_MONITOR_PILOT_OPERATOR_TOKEN;
const workId = process.env.BRIDGE_MONITOR_PILOT_WORK_ID;
const expectedResolution = process.env.BRIDGE_MONITOR_PILOT_EXPECTED_RESOLUTION;
if (!baseURL || !token || !workId) throw new Error('pilot URL, operator token and work ID are required');
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1512, height: 950 } });
  await page.goto(baseURL);
  await page.getByLabel('Operator credential').fill(token);
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('observed');
  await page.getByLabel('Search work items').fill(workId);
  await page.getByRole('button', { name: new RegExp(workId) }).click();
  await page.getByRole('tab', { name: 'exchange' }).click();
  await page.getByText('Response delivered to caller').waitFor();
  await page.locator('.inspector').getByText('56', { exact: false }).first().waitFor();
  if (expectedResolution) await page.locator('.inspector').getByText(expectedResolution, { exact: true }).waitFor();
  const screenshot = expectedResolution === 'paid_direct' ? 'pilot-paid-direct.png' : 'pilot-history.png';
  await page.screenshot({ path: `../var/reports/dashboard/${screenshot}`, fullPage: true });
  const content = await page.locator('.inspector').innerText();
  if (!content.includes('56') || !content.includes('Original prompt')) throw new Error('historical exchange was not inspectable');
  if (expectedResolution && !content.includes(expectedResolution)) throw new Error('work resolution was not inspectable');
  console.log(JSON.stringify({ workId, historicalContentVisible: true, resolution: expectedResolution,
    screenshot: `../var/reports/dashboard/${screenshot}` }));
} finally {
  await browser.close();
}
