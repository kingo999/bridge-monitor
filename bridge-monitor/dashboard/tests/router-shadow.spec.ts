import { expect, test } from '@playwright/test';

test('router cohort remains separate from production cards and audit opens per work item', async ({ page }) => {
  await page.route('**/api/v1/router/cohort?*', async route => {
    await route.fulfill({ json: {
      definition_version: 'parent-router-shadow-v1', attempted_tasks: 3,
      queued_shadow: 2, result_captured: 2, independent_evaluated: 2,
      states: { completed: 2, not_queued: 1 }, outcomes: { pass: 1, fail: 1 },
      reasons: { tests_passed: 1, tests_failed: 1, verifier_missing: 1 },
      model_profiles: { 'qwen-test': 3 }, prompt_versions: { 'complete-module-v1': 3 },
      verifier_refs: { verified: 2, missing: 1 },
      categories: { single_file_python_fix: 3 },
      output_contracts: { complete_python_module_v1: 3 },
      context_bytes: { samples: 3, median: 200 },
      queue_latency_ms: { samples: 2, median: 100 },
      shadow_execution_latency_ms: { samples: 2, median: 500 },
      verifier_latency_ms: { samples: 2, median: 20 },
      usage_coverage: { qwen: 1, paid: 1 }, unknown_usage: { qwen: 1, paid: 2 },
      provider_usage: [], conditional_avoided_paid_calls: null,
      conditional_savings_reason: 'no_comparable_paid_same_contract_reference',
      billed_savings: null, billed_savings_reason: 'billing_not_observed',
    } });
  });
  await page.route('**/api/v1/router/audit/demo-work-061', async route => {
    await route.fulfill({ json: {
      work_item: { work_item_id: 'demo-work-061' }, paid_call_id: 'paid-1',
      shadow_call_id: 'shadow-1', parent_decision: { route: 'paid',
        reason: 'shadow_candidate', gate_facts: { prompt_version: 'complete-module-v1' } },
      shadow_decision: { reason: 'shadow_candidate', gate_facts: {} },
      decisions: [], jobs: [{ call_id: 'shadow-1', pool: 'qwen', execution_mode: 'shadow',
        state: 'completed', requested_model: 'qwen-test', admitted_at: new Date().toISOString(),
        launch_claimed_at: new Date().toISOString(), slot_held: false }],
      paid_result: { result_id: 'paid-result', delivered_at: new Date().toISOString() },
      shadow_result: { result_id: 'shadow-result', delivered_at: null },
      shadow_parts: [], evaluation: { state: 'pass', reason: 'tests_passed',
        verifier_image: `python@sha256:${'a'.repeat(64)}`, elapsed_seconds: 0.2,
        recorded_at: new Date().toISOString() },
      evaluation_history: [],
      prompt_refs: { original: null, paid_effective: null, shadow_effective: null },
      result_refs: { paid: null, shadow_raw: null, shadow_parsed: null },
      dispositions: [], usage: [], timeline: [],
    } });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByRole('region', { name: 'Percentage panel', exact: true }).locator('summary').click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  const cohort = page.getByRole('region', { name: 'Parent router shadow cohort' });
  await cohort.locator('summary').click();
  await expect(cohort).toContainText('3');
  await expect(cohort).toContainText('Qwen shadow queued');
  await expect(cohort).toContainText('Trusted-test passes');
  await expect(cohort).toContainText('Billed savings: Unknown');
  const filtered = page.waitForRequest(request => request.url().includes('/router/cohort?') &&
    request.url().includes('model_profile=qwen-test'));
  await page.getByLabel('Router model profile').fill('qwen-test');
  await filtered;
  const production = await (await page.request.get('/api/v1/metrics?origin=synthetic')).json();
  const routed = production.metrics.routed;
  await expect(page.getByRole('button', { name: /Routed to Skynet/ }))
    .toContainText(`${routed.numerator} / ${routed.denominator}`);
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  await page.getByRole('tab', { name: 'router' }).click();
  const audit = page.getByRole('region', { name: 'Parent router audit' });
  await expect(audit).toContainText('shadow_candidate');
  await expect(audit).toContainText('tests_passed');
  await expect(audit).toContainText('Shadow output was never delivered to Claude');
});

test('dashboard remains available before the router backend is rolled out', async ({ page }) => {
  await page.route('**/api/v1/router/cohort?*', route => route.fulfill({ status: 404 }));
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByRole('region', { name: 'Percentage panel', exact: true }).locator('summary').click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await expect(page.getByRole('button', { name: /Routed to Skynet/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Parent router shadow cohort' })).toHaveCount(0);
  await expect(page.getByText(/Data error:/)).toHaveCount(0);
});
