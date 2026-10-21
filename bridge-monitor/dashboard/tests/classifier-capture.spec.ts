import { expect, test } from '@playwright/test';

test('classifier capture filter and unknown capacity stay separate from production totals', async ({ page }) => {
  const dist = { samples: 0, median: null, p95: null, max: null };
  const methods: string[] = [];
  await page.route('**/api/v1/classifier/cohort?*', route => {
    methods.push(new URL(route.request().url()).searchParams.get('capture_method') || 'all');
    return route.fulfill({ json: {
    definition_version: 'classifier-shadow-cohort-v1', available: true,
    scope_note: 'Advisory recommendations only.',
    counts: { submitted: 2, attempted: 2, completed: 2, failed: 0, skipped: 0, uncertain: 0,
      cancelled: 0, pending: 0, unknown_outcome: 0 },
    recommendations: { routes: { code_local: 2 }, categories: { code: 2 } },
    capture: { observations: { manual: 1, route_work: 1 }, unique_work_items: 1,
      unique_work_items_by_method: { manual: 1, route_work: 1 }, overlap_work_items: 1,
      note: 'Dropped in-memory offers are not recorded.' },
    fact_coverage: { code_available: { true: 0, false: 1, unknown: 1 } },
    policy: { allowed: 0, blocked: 2, block_reasons: { code_worker_unknown: 1, code_worker_unavailable: 1 },
      unknown_code_capacity_blocks: 1, other_blocked: 1 },
    reference: { labelled: 0, unlabelled: 2, raw_route_acceptable: 0, allowed_and_acceptable: 0 },
    actual_route: { known: 1, unknown: 1, differs_from_recommendation: 1 },
    follow_up: { with_verification_or_correction: 0, completed: 2, with_operator_judgment: 0 },
    skip_reasons: {}, error_types: {}, contract_errors: {},
    latency_ms: { queue: dist, request: dist, end_to_end: dist },
    tokens: { input_total: 1400, output_total: 80, provenance: { ollama_terminal: 2 } },
    backend_residency: { gpu_resident: 2 },
    cost: { local_provider_charge_usd: 0, electricity_usd: null, electricity_reason: 'not_measured',
      hypothetical_avoided_paid_calls: null, billed_savings: null },
    filters: { models: ['gemma4:12b'], profiles: ['gemma-router-profile-v1'], rubrics: [], origins: ['natural'] },
    } });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByRole('region', { name: 'Percentage panel', exact: true }).locator('summary').click();
  const panel = page.getByRole('region', { name: 'Gemma classifier shadow cohort' });
  await panel.locator('summary').click();
  await expect(panel).toContainText('1 unique work items');
  await expect(panel).toContainText('Unknown code capacity blocks: 1');
  await expect(panel).toContainText('Unknown capacity is not evidence of an incorrect raw route.');
  expect(methods[0]).toBe('all');
  const manual = page.waitForRequest(r => r.url().includes('/classifier/cohort?') &&
    r.url().includes('capture_method=manual'));
  await page.getByLabel('Classifier capture method').selectOption('manual');
  await manual;
  const filtered = page.waitForRequest(r => r.url().includes('/classifier/cohort?') &&
    r.url().includes('capture_method=route_work'));
  await page.getByLabel('Classifier capture method').selectOption('route_work');
  await filtered;
  await expect(page.getByRole('button', { name: /Routed to Skynet/ })).toBeVisible();
});
