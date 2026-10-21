import { expect, test } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';

function workId(executionId: string) {
  const bytes = createHash('sha1').update(Buffer.from('6ba7b8119dad11d180b400c04fd430c8', 'hex')).update(executionId).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 15) | 80; bytes[8] = (bytes[8] & 63) | 128;
  const hex = bytes.toString('hex');
  return `direct:${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

test('direct execution shows bypass, unknown usage and a late CLI receipt without affecting Skynet routing', async ({ page, request }) => {
  const execution = randomUUID(); const work = workId(execution);
  const report = { record_id: randomUUID(), execution_id: execution, session_id: `session-${execution}`,
    project_root: 'C:/test/project', summary: 'Direct task display check', status: 'completed',
    bypass_reason: 'user_instruction', reason: 'Codex directly', requested_model: 'requested-test-model',
    observed_at: new Date().toISOString() };
  const event = (sequence: number, usage: Record<string, unknown> | null = null) => ({
    event_id: `${execution}-${sequence}`, event_type: 'direct_task.recorded', schema_version: 1,
    producer_id: 'seed', producer_incarnation: execution, producer_sequence: sequence,
    occurred_at: report.observed_at, origin: 'imported', observation_layer: 'e2e',
    capture_completeness: 'partial', task_id: work, work_item_id: work, session_id: report.session_id,
    payload: { report: { ...report, record_id: `${execution}-${sequence}` }, usage,
      source_turn_id: usage ? 'turn-1' : null },
  });
  const headers = { Authorization: 'Bearer producer-secret' };
  const unauthorized = await request.post('/api/v1/events/batch', { data: { events: [event(1)], content: [] } });
  expect(unauthorized.status()).toBe(401);
  const recorded = await request.post('/api/v1/events/batch', { headers, data: { events: [event(1)], content: [] } });
  expect(recorded.ok()).toBeTruthy();
  await page.goto('/');
  await page.getByLabel('Operator credential').fill('operator-secret');
  await page.getByRole('button', { name: 'Open dashboard' }).click();
  await page.locator('.toolbar').getByLabel('Origin').selectOption('imported');
  await page.getByLabel('Execution path', { exact: true }).selectOption('direct_codex');
  await page.getByRole('button', { name: new RegExp(work) }).click();
  const panel = page.getByRole('region', { name: 'Direct Codex execution', exact: true });
  await expect(panel).toContainText('Bypass reason: user_instruction');
  await expect(panel).toContainText('Token usage: Unknown');
  await expect(page.getByRole('row').filter({ hasText: work })).toContainText('Not applicable');
  await page.getByLabel('Execution path', { exact: true }).selectOption('bridge');
  await expect(page.getByRole('button', { name: new RegExp(work) })).toHaveCount(0);
  await page.getByLabel('Execution path', { exact: true }).selectOption('direct_codex');
  const usage = { session_id: report.session_id, source_sha256: 'a'.repeat(64), scope: 'turn',
    provenance: 'codex_cli_receipt', input_tokens: 23, output_tokens: 0,
    cache_read_input_tokens: 11, cache_creation_input_tokens: null, reasoning_output_tokens: null };
  report.observed_at = new Date().toISOString();
  const late = await request.post('/api/v1/events/batch', { headers, data: { events: [event(2, usage)], content: [] } });
  expect(late.ok()).toBeTruthy();
  await expect(panel).toContainText('Input: 23');
  await expect(panel).toContainText('Output: 0');
  await expect(panel).toContainText('Cache-creation input: Unknown');
  await expect(panel).toContainText('Provider: Unknown');
  const desktopUsage = { ...usage, provenance: 'codex_desktop_usage_record',
    source_sha256: 'b'.repeat(64), runtime_model: 'desktop-runtime-model', runtime_provider: 'openai' };
  const desktop = event(3, desktopUsage);
  desktop.payload.source_turn_id = 'turn-2';
  const captured = await request.post('/api/v1/events/batch', { headers, data: { events: [desktop], content: [] } });
  expect(captured.ok()).toBeTruthy();
  await expect(panel).toContainText('Codex desktop runtime usage');
  await expect(panel).toContainText('Runtime model: desktop-runtime-model');
  await expect(panel).toContainText('Runtime provider: openai');
  await expect(panel).toContainText('Source SHA-256 (turn range)');
  await page.screenshot({ path: '../var/reports/direct-execution-dashboard.png', fullPage: true });
});
