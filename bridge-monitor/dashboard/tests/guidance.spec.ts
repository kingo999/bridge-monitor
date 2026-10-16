import { expect, test } from '@playwright/test';

test('guidance filter and inspector distinguish actual baseline and reviewed supplied context', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.getByLabel('Origin').selectOption('synthetic');
  await page.getByLabel('Guidance group').fill('no_guidance');
  await expect(page.getByText('40 work items')).toBeVisible();
  await page.getByLabel('Guidance group').fill('not_dispatched');
  await expect(page.getByText('60 work items')).toBeVisible();
  await page.getByLabel('Guidance group').fill('');

  await page.route('**/api/v1/work-items/demo-work-061', async route => {
    const response = await route.fetch();
    const detail = await response.json();
    detail.decisions[0].guidance_manifest = {
      state: 'supplied', pack_id: 'repo-rules', pack_version_id: 'rules-v1', pack_sha256: 'abc123',
      selection_source: 'configuration', supplied: [{ item_id: 'example-1', kind: 'example',
        sha256: 'example-hash', review_id: 'review-1', source_work_item_id: 'source-work',
        source_result_id: 'source-result' }], excluded: [{ item_id: 'example-2', reason: 'example_not_approved_for_prompt' }],
    };
    detail.decisions[0].gate_facts = { readiness: 'pass', current_policy_version: 'policy-v1' };
    detail.dispositions[0].details = { failure_category: 'incorrect_behavior', repair_effort: 'minor',
      guidance_ids: ['example-1'], correction_execution_ids: ['claude-fix-1'] };
    detail.example_reviews = [{ review_id: 'review-1', pack_version_id: 'rules-v1', example_id: 'example-1',
      content_id: 'example-content', source_work_item_id: 'source-work', source_result_id: 'source-result',
      reviewer_id: 'human-1', status: 'approved', permitted_use: 'prompt_example', exposure: 'unknown',
      scope: ['read'], evidence: { validation: { outcome: 'pass' } }, supersedes_id: null,
      reviewed_at: '2026-10-16T10:00:00Z' }];
    detail.evaluations = [{ evaluation_id: 'eval-1', result_id: 'demo-result-061',
      evidence: { check: 'manual', outcome: 'pass' }, recorded_at: '2026-10-16T10:01:00Z' }];
    detail.result_links = [{ from_result_id: 'demo-result-061', to_result_id: 'paid-result-061',
      relation: 'correction' }];
    await route.fulfill({ response, json: detail });
  });
  await page.getByRole('button', { name: /demo-work-061/ }).click();
  await page.getByRole('tab', { name: 'guidance' }).click();
  await expect(page.getByRole('heading', { name: 'Guidance' })).toBeVisible();
  await expect(page.getByText('repo-rules', { exact: false })).toBeVisible();
  await expect(page.getByText('example_not_approved_for_prompt')).toBeVisible();
  await expect(page.getByText('Reuse approved · prompt_example')).toBeVisible();
  await expect(page.getByText('incorrect_behavior')).toBeVisible();
  await expect(page.getByText('claude-fix-1')).toBeVisible();
  await expect(page.getByText('Validation · result demo-result-061')).toBeVisible();
});
