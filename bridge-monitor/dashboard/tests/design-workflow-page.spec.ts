import { existsSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

// The lease isolates LOCALAPPDATA, so Playwright cannot find Edge itself. Use the installed system Edge.
const systemEdge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
if (process.platform === 'win32' && process.env.BRIDGE_MONITOR_PLAYWRIGHT_CHANNEL === 'msedge' && existsSync(systemEdge)) {
  test.use({ launchOptions: { executablePath: systemEdge } });
}

// Route-mocked fixtures: these tests prove presentation only. Filtering and ownership are
// proven against PostgreSQL in tests/integration/test_design_mode.py.
const UNAVAILABLE = 'Decision filter unavailable: configure a Design Mode owner.';
const EMPTY = 'No workflows awaiting your decision';
type Row = Record<string, unknown> & { workflow_id: string; repository_id: string };
const row = (id: string, extra: Record<string, unknown> = {}): Row => ({ workflow_id: id, request_key: 'key-' + id,
  repository_id: 'demo', project_root: 'C:/fixture', current_iteration: 1, version: 3, status: 'delivered',
  status_actor: 'chat', status_reason: null, updated_at: '2026-11-02T12:00:00Z', owner_id: 'fixture@host',
  outcome_label: 'Delivered to Chat on iteration 1', accepted_count: 0, rejected_count: 0, revision_count: 0, ...extra });
interface Mock { configured: boolean; available?: boolean; fail?: boolean; all: Row[]; awaiting: Row[];
  detailGate?: Promise<void>; detailServed: number }

async function login(page: Page) {
  const login = await page.request.post('/api/v1/session', { data: { operator_token: 'operator-secret' } });
  expect(login.ok()).toBeTruthy();
}

async function mock(page: Page, state: Mock, queries: string[]) {
  await page.route('**/api/v1/design-mode/workflows**', async route => {
    const url = new URL(route.request().url());
    const id = url.pathname.split('/design-mode/workflows/')[1];
    if (id) {
      const w = [...state.all, ...state.awaiting].find(x => x.workflow_id === decodeURIComponent(id));
      if (state.detailGate) await state.detailGate;
      await route.fulfill({ json: { ...w, iterations: [], evidence: [], audit: [], deliveries: [], artifacts: [], hops: [] } });
      state.detailServed++;
      return;
    }
    queries.push(url.search);
    if (state.fail) return route.fulfill({ status: 500, body: 'boom' });
    if (state.available === false) return route.fulfill({ json: { available: false, workflows: [], projects: [] } });
    const filtered = url.searchParams.get('awaiting_my_decision') === 'true';
    const repo = url.searchParams.get('repository_id');
    let rows = filtered ? (state.configured ? state.awaiting : []) : state.all;
    if (repo) rows = rows.filter(r => r.repository_id === repo);
    await route.fulfill({ json: { available: true, decision_filter_configured: state.configured, workflows: rows,
      projects: [{ repository_id: 'demo', project_root: 'C:/fixture' }, { repository_id: 'other', project_root: 'C:/other' }] } });
  });
}

function hide(page: Page, hidden: boolean) {
  return page.evaluate(h => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => h });
    document.dispatchEvent(new Event('visibilitychange')); }, hidden);
}

test('operator can inspect a review hop and filter design workflows by project', async ({ page }) => {
  await login(page);
  const w = { workflow_id:'dm-ui-example',repository_id:'demo',project_root:'C:/fixture',current_iteration:2,
    version:7,status:'accepted',status_actor:'chat',status_reason:'ready to implement',updated_at:'2026-11-02T12:00:00Z',
    owner_id:'fixture',outcome_label:'Accepted on iteration 2',accepted_count:1,rejected_count:0,revision_count:1 };
  const queries: string[] = [];
  await page.route('**/api/v1/design-mode/workflows**', async route => {
    const url=new URL(route.request().url()); queries.push(url.search);
    const payload=url.pathname.endsWith('/dm-ui-example') ? {...w, iterations:[{iteration:2}],evidence:[],audit:[],
      deliveries:[],artifacts:[],hops:[{iteration:2,from_actor:'codex',to_actor:'claude',sequence:2,kind:'plan',
      state:'completed',attempt:1,worker_call_id:'fixture-review-call'}]}
      : {available:true,decision_filter_configured:true,projects:[{repository_id:'demo',project_root:'C:/fixture'}],workflows:[w]};
    await route.fulfill({json:payload});
  });
  await page.goto('/api/v1/design-mode/view');
  await expect(page.locator('#list')).toContainText('Accepted on iteration 2');
  await page.getByText(w.workflow_id,{exact:true}).click();
  await expect(page.locator('#detail')).toContainText('fixture-review-call');
  await page.getByLabel('Project').selectOption('demo');  // name includes nested option text
  await expect.poll(()=>queries.includes('?repository_id=demo')).toBeTruthy();
  await expect(page.locator('#detail')).toBeEmpty();
});

test('awaiting my decision: keyboard, combined project filter, empty copy, row removal and late detail', async ({ page }) => {
  await login(page);
  await page.clock.install();
  const a = row('dm-awaiting'), b = row('dm-elsewhere', { repository_id: 'other', project_root: 'C:/other' });
  const ready = row('dm-ready', { status: 'ready', outcome_label: 'Ready for Chat' });
  const state: Mock = { configured: true, all: [ready, a, b], awaiting: [a, b], detailServed: 0 };
  const queries: string[] = [];
  await mock(page, state, queries);
  await page.goto('/api/v1/design-mode/view');
  const box = page.getByLabel('Awaiting my decision', { exact: true });
  const status = page.locator('#filter-status');
  await expect(box).toBeEnabled();
  await expect(box).not.toBeChecked();
  await expect(box).toHaveAccessibleDescription(/Shown after Chat delivers a plan and review, until you accept, revise or reject\./);
  expect(queries[0]).toBe('');  // default request URL unchanged
  await page.getByLabel('Project').selectOption('demo');
  await expect.poll(() => queries.includes('?repository_id=demo')).toBeTruthy();
  await box.focus();
  await page.keyboard.press('Space');
  await expect(box).toBeChecked();
  await expect.poll(() => queries.includes('?repository_id=demo&awaiting_my_decision=true')).toBeTruthy();
  await expect(page.locator('#list tbody tr')).toHaveCount(1);
  await expect(page.locator('#list tbody')).toContainText('dm-awaiting');
  await expect(status).toHaveText('');
  await page.getByRole('button', { name: 'dm-awaiting', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#detail')).toContainText('Workflow dm-awaiting');
  // Chat decides elsewhere: the row leaves the filtered list while the refresh's detail reply is held back.
  const served = state.detailServed;
  let release!: () => void;
  state.detailGate = new Promise<void>(r => { release = r; });
  state.awaiting = [b];
  await page.clock.runFor(10_000);
  await expect(status).toHaveText(EMPTY);
  await expect(page.locator('#list tbody tr')).toHaveCount(0);
  await expect(page.locator('#detail')).toBeEmpty();
  release();
  await expect.poll(() => state.detailServed).toBe(served + 1);
  await page.waitForTimeout(250);
  await expect(page.locator('#detail')).toBeEmpty();  // the late reply cannot resurrect it
  state.detailGate = undefined;
  // Combined filters: all projects + awaiting shows only the other project's delivered row.
  await page.getByLabel('Project').selectOption('');
  await expect.poll(() => queries.at(-1)).toBe('?awaiting_my_decision=true');
  await expect(page.locator('#list tbody tr')).toHaveCount(1);
  await expect(page.locator('#list tbody')).toContainText('dm-elsewhere');
  await expect(status).toHaveText('');
  await box.focus();
  await page.keyboard.press('Space');
  await expect(box).not.toBeChecked();
  await expect.poll(() => queries.at(-1)).toBe('');
  await expect(page.locator('#list tbody tr')).toHaveCount(3);
});

test('decision filter unavailable: persistent explanation, lost binding, hidden and visible refresh', async ({ page }) => {
  await login(page);
  await page.clock.install();
  const a = row('dm-awaiting');
  const state: Mock = { configured: true, all: [a], awaiting: [a], detailServed: 0 };
  const queries: string[] = [];
  await mock(page, state, queries);
  await page.goto('/api/v1/design-mode/view');
  const box = page.getByLabel('Awaiting my decision', { exact: true });
  const status = page.locator('#filter-status');
  await expect(box).toBeEnabled();
  await box.focus();
  await page.keyboard.press('Space');
  await expect.poll(() => queries.at(-1)).toBe('?awaiting_my_decision=true');
  // The binding disappears (service reloaded without it): disable, clear and reload unfiltered once.
  state.configured = false;
  const before = queries.length;
  await page.clock.runFor(10_000);
  await expect(box).toBeDisabled();
  await expect(box).not.toBeChecked();
  await expect(status).toHaveText(UNAVAILABLE);
  await expect.poll(() => queries.slice(before)).toEqual(['?awaiting_my_decision=true', '']);
  await expect(page.locator('#list tbody')).toContainText('dm-awaiting');
  // Hidden page: no refresh. Visible again: immediate refresh, then every 10 s; explanation persists.
  await hide(page, true);
  const hiddenAt = queries.length;
  await page.clock.runFor(30_000);
  expect(queries.length).toBe(hiddenAt);
  await hide(page, false);
  await expect.poll(() => queries.length).toBe(hiddenAt + 1);
  await page.clock.runFor(10_000);
  await expect.poll(() => queries.length).toBe(hiddenAt + 2);
  await expect(status).toHaveText(UNAVAILABLE);
  await expect(page.locator('#error')).toHaveText('');
  expect(queries.slice(hiddenAt)).toEqual(['', '']);
});

test('errors and missing schema take precedence over decision-filter copy', async ({ page }) => {
  await login(page);
  await page.clock.install();
  const state: Mock = { configured: false, all: [], awaiting: [], detailServed: 0 };
  const queries: string[] = [];
  await mock(page, state, queries);
  await page.goto('/api/v1/design-mode/view');
  const box = page.getByLabel('Awaiting my decision', { exact: true });
  const status = page.locator('#filter-status');
  await expect(status).toHaveText(UNAVAILABLE);
  await expect(box).toBeDisabled();
  state.fail = true;
  await page.clock.runFor(10_000);
  await expect(page.locator('#error')).toHaveText(/^500/);
  await expect(status).toHaveText('');
  state.fail = false;
  state.available = false;
  await page.clock.runFor(10_000);
  await expect(page.locator('#error')).toHaveText('Design Mode tables are not migrated.');
  await expect(status).toHaveText('');
  await expect(box).toBeDisabled();
  state.available = true;
  state.configured = true;
  await page.clock.runFor(10_000);
  await expect(page.locator('#error')).toHaveText('');
  await expect(box).toBeEnabled();
  await box.focus();
  await page.keyboard.press('Space');
  await expect(status).toHaveText(EMPTY);
});
