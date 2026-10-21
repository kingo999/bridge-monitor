import { expect, test } from '@playwright/test';

test('reconciliation refresh shows released capacity and receipt while preserving timeout history', async ({ page }) => {
  let reconciled = false;
  await page.route('**/api/v1/work-items/demo-work-061', async route => {
    const response = await route.fetch();
    const detail = await response.json();
    const call = detail.calls.find((c: { local_dispatch: boolean }) => c.local_dispatch);
    detail.agent_statuses = [{ call_id: call.call_id, agent_state: 'ollama_connecting',
      agent_reachable: true, last_model_output_at: null, timeout_reason: 'first_output_deadline' }];
    detail.runtime_executions = [{ call_id: call.call_id, pool: 'qwen',
      state: reconciled ? 'terminated' : 'fallback', slot_held: !reconciled,
      receipts_truncated: false, receipts: reconciled ? [{ reconciliation_id: 'receipt-ui-1',
        call_id: call.call_id, observed_state: 'terminated', recorded_at: '2026-10-21T10:50:00Z',
        actor_kind: 'operator', actor_id: 'test-operator', evidence: {
          kind: 'adapter_termination_receipt', provenance: 'operator_correlated_server_abort',
          basis: 'Server confirmed request abort', terminated: true, source_sha256: 'a'.repeat(64),
          historical_runner_pid: null,
        } }] : [] }];
    await route.fulfill({ response, json: detail });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  const inspector = page.getByRole('complementary', { name: 'Selected work item' });
  const current = inspector.getByRole('region', { name: 'Recorded execution state' });
  await expect(current).toContainText('Slot: Held');
  await page.getByRole('button', { name: 'Pause display' }).click();
  reconciled = true;
  await page.waitForTimeout(5500);
  await expect(current).toContainText('Slot: Held');
  await page.getByRole('button', { name: 'Resume display' }).click();
  await expect(current).toContainText('Reconciled: terminated', { timeout: 10000 });
  await expect(current).toContainText('Slot: Released');
  await expect(inspector.locator('.inspector-summary')).toContainText('first_output_deadline');
  await expect(inspector.locator('.inspector-summary')).toContainText('ollama_connecting');
  await inspector.getByRole('tab', { name: 'decision', exact: true }).click();
  await inspector.getByText('Reconciliation receipt receipt-ui-1', { exact: true }).click();
  await expect(inspector).toContainText('operator_correlated_server_abort');
  await expect(inspector).toContainText('Server confirmed request abort');
  await expect(inspector).toContainText('test-operator');
  await expect(inspector).toContainText('Runner PID: Unknown');
});

test('missing runtime evidence never implies a released slot', async ({ page }) => {
  await page.route('**/api/v1/work-items/demo-work-061', async route => {
    const response = await route.fetch();
    const detail = await response.json();
    delete detail.runtime_executions;
    await route.fulfill({ response, json: detail });
  });
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('synthetic');
  await page.getByRole('button', { name: /demo-work-061/ }).first().click();
  const inspector = page.getByRole('complementary', { name: 'Selected work item' });
  await expect(inspector).toContainText('Qwen original');
  await expect(inspector).not.toContainText('Slot: Released');
});
