import { expect, test, type Page } from '@playwright/test';

// Authenticated real cohort response; only report.efficiency is replaced with a fixture so the
// rendering of complete, partial/unknown and legacy (missing efficiency) data is deterministic.
const outcomes = (values: Record<string, number>) => ({ accepted: 0, revised: 0, replaced: 0, unused: 0,
  pending: 0, ambiguous: 0, unknown: 0, ...values });
const counter = (known_sum: number | null, covered: number, unknown: number) =>
  ({ known_sum, covered_workflows: covered, unknown_workflows: unknown });
const efficiency = {
  definition_version: 'codex-claude-efficiency-v1', unit: 'workflows',
  group_by: ['applied_preset', 'reported_model', 'configured_effort'], parent_coordinator_overhead: 'unmeasured',
  note: 'Workflow counts, not calls.',
  groups: [
    { applied_preset: 'legacy_no_preset', reported_model: 'unknown', configured_effort: 'unattributed', workflows: 3,
      outcomes: outcomes({ pending: 1, unknown: 2 }), outcome_denominator: 3, human_accepted: null, checks_passed: null,
      duration_seconds: { samples: 1, median: 5, p95: 5 },
      counters: { input_tokens: counter(null, 0, 3), output_tokens: counter(17, 1, 2),
        cache_read_input_tokens: counter(null, 0, 3), cache_creation_input_tokens: counter(null, 0, 3) },
      api_equivalent_usd: { basis: 'USD API-equivalent', known_subtotal: null, covered_workflows: 0,
        unknown_workflows: 3, is_total_job_cost: false } },
    { applied_preset: 'standard', reported_model: 'claude-sonnet-test', configured_effort: 'medium', workflows: 2,
      outcomes: outcomes({ accepted: 1, revised: 1 }), outcome_denominator: 2, human_accepted: null, checks_passed: null,
      duration_seconds: { samples: 2, median: 12.5, p95: 20 },
      counters: { input_tokens: counter(0, 2, 0), output_tokens: counter(340, 2, 0),
        cache_read_input_tokens: counter(0, 2, 0), cache_creation_input_tokens: counter(12, 1, 1) },
      api_equivalent_usd: { basis: 'USD API-equivalent', known_subtotal: '0', covered_workflows: 2,
        unknown_workflows: 0, is_total_job_cost: false } },
  ],
};

async function openWith(page: Page, mutate: (body: Record<string, unknown>) => void) {
  const statuses: number[] = [];
  await page.route('**/api/v1/codex-claude/cohort*', async route => {
    const real = await route.fetch();
    statuses.push(real.status());
    if (!real.ok()) return route.fulfill({ response: real });
    const body = await real.json();
    mutate(body);
    await route.fulfill({ response: real, json: body });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await expect(page.getByRole('heading', { name: 'Codex / Claude overview' })).toBeVisible();
  await expect.poll(() => statuses.length).toBeGreaterThan(0);
  expect(statuses.every(status => status === 200)).toBe(true);
  const cookies = await page.context().cookies();
  expect(cookies.find(cookie => cookie.name === 'bridge_monitor_session')?.httpOnly).toBe(true);
  return page.getByRole('region', { name: 'Efficiency comparison' });
}

// The overview refreshes periodically; drop in-flight intercepted refreshes at teardown.
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'ignoreErrors' }); });

test('cohort report stays protected without a session', async ({ request }) => {
  expect((await request.get('/api/v1/codex-claude/cohort?origin=all')).status()).toBe(401);
});

test('efficiency comparison shows complete, partial and true-zero evidence without ranking', async ({ page }) => {
  const region = await openWith(page, body => { body.efficiency = efficiency; });
  await expect(region).toContainText('not total job cost');
  await expect(region).toContainText('subscription bill');
  await expect(region).toContainText('Parent coordinator overhead: unmeasured');
  await expect(region).not.toContainText(/cheapest|best model|savings/i);
  const complete = region.getByRole('row').filter({ hasText: 'claude-sonnet-test' });
  await expect(complete).toContainText('standard');
  await expect(complete).toContainText('medium');
  await expect(complete).toContainText('accepted 1');
  await expect(complete).toContainText('revised 1');
  await expect(complete).toContainText('replaced 0');
  await expect(complete).toContainText('of 2 workflows');
  await expect(complete).toContainText('Human verification: Unknown');
  await expect(complete).toContainText('12.5s');
  await expect(complete).toContainText('20.0s');
  await expect(complete).toContainText('n=2');
  await expect(complete).toContainText('Input: 0 (2 covered · 0 unknown)');
  await expect(complete).toContainText('Output: 340');
  await expect(complete).toContainText('Cache creation: 12 (1 covered · 1 unknown)');
  await expect(complete.locator('td').nth(7)).toContainText('$0.0000');
  await expect(complete.locator('td').nth(7)).toContainText('2 receipt-covered · 0 unknown');
  const partial = region.getByRole('row').filter({ hasText: 'Legacy (no preset)' });
  await expect(partial).toContainText('Unknown model');
  await expect(partial).toContainText('Unattributed');
  await expect(partial).toContainText('pending 1');
  await expect(partial).toContainText('unknown 2');
  await expect(partial).toContainText('Input: Unknown (0 covered · 3 unknown)');
  await expect(partial).toContainText('Output: 17 (1 covered · 2 unknown)');
  await expect(partial.locator('td').nth(7)).toContainText('Unknown');
  await expect(partial.locator('td').nth(7)).not.toContainText('$');
  await expect(partial.locator('td').nth(7)).toContainText('0 receipt-covered · 3 unknown');
  await expect(page.locator('.cc-stats')).toContainText('Observed workflows');
});

test('older backend without efficiency renders an unavailable state, not zeros', async ({ page }) => {
  const region = await openWith(page, body => { delete body.efficiency; });
  await expect(region).toContainText('Efficiency comparison unavailable');
  await expect(region).not.toContainText('$0');
  await expect(page.locator('.cc-stats')).toContainText('Observed workflows');
  await expect(page.getByRole('link', { name: 'Export CSV' })).toHaveAttribute('href', /as_of=/);
});
