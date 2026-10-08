import { chromium, expect } from '/home/hp/workspace/HpAgent_web/web/node_modules/@playwright/test/index.mjs';
import { writeFile } from 'node:fs/promises';
const base = new URL('.', import.meta.url).pathname;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
let requested = false, release;
const gate = new Promise(resolve => { release = resolve; });
let first = true;
const snapshot = { source_kind: 'work', run: { source_kind: 'work', run_id: 'run-A', execution_id: 'exec-A', work_id: 'work-A', requirement_revision: 1, work_control_epoch: 1, conversation_id: null, session_id: null, status: 'succeeded', version: 1, created_at: '2026-10-08T00:00:00Z', started_at: null, finished_at: '2026-10-08T00:00:01Z', updated_at: '2026-10-08T00:00:01Z', failure_code: null, failure_message: null, strategy_kind: 'generic_agent', executor_key: 'general', result_json: {}, budget: null, branches: [] } };
const calls = [];
await page.route('**/api/v1/**', async route => {
  const path = new URL(route.request().url()).pathname;
  calls.push({ path, method: route.request().method() });
  let json = { items: [], next_cursor: null, has_more: false };
  if (path === '/api/v1/me') json = { account: { account_id: 'review', status: 'active', created_at: '2026-10-08T00:00:00Z' }, session: {}, csrf_token: 'fixture', identities: { web: { username: 'review' }, qq: { bound: false } }, capabilities: { durable_agent: true, file_upload: true } };
  if (path === '/api/v1/workspace') json = { workspace_id: 'space', root_id: 'root', nodes: [{ node_id: 'root', parent_id: null, kind: 'directory', name: '', file_id: null, source: null }] };
  if (path === '/api/v1/notifications') json = { items: [{ notification_id: 'notice', payload: { content: '本次检查通知' }, created_at: '2026-10-08T00:00:00Z', work_id: null, run_id: null }] };
  if (path === '/api/v1/runs/run-A') { requested = true; if (first) { first = false; await gate; } json = snapshot; }
  await route.fulfill({ json });
});
try {
  await page.goto('http://127.0.0.1:5288/#/tasks');
  await page.getByRole('button', { name: '收件箱', exact: true }).click();
  await expect(page.getByText('本次检查通知', { exact: true })).toBeVisible();
  const dialog = page.getByRole('dialog', { name: '账户收件箱', exact: true });
  await dialog.locator('h2').focus();
  const order = [];
  for (let n = 0; n < 8; n++) {
    await page.keyboard.press('Tab');
    order.push(await page.evaluate(() => ({ tag: document.activeElement?.tagName, text: document.activeElement?.textContent?.trim() })));
  }
  const summaryReached = order.some(v => v.tag === 'SUMMARY' && v.text === '通知详情');
  await page.screenshot({ path: base + 'R2-inbox-keyboard.png' });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'AI', exact: true }).click();
  await page.getByText('按执行编号查询', { exact: true }).click();
  await page.getByLabel('执行编号', { exact: true }).fill('run-A');
  await page.getByRole('button', { name: '查询', exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  await page.getByRole('button', { name: '账户设置', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '账户设置', exact: true })).toBeVisible();
  release();
  await expect(page.locator('.hp-inspector')).toBeVisible();
  const accountPreserved = await page.getByRole('dialog', { name: '账户设置', exact: true }).count() === 1;
  await page.screenshot({ path: base + 'R1-late-lookup.png' });
  const result = { scope: 'Chromium + local Vite + API response fixtures; no backend or business mutation', R1: { expectedAccountPreserved: true, actualAccountPreserved: accountPreserved, runInspectorVisible: await page.locator('.hp-inspector').isVisible() }, R2: { expectedSummaryReachableByTab: true, actualSummaryReached: summaryReached, tabOrder: order }, calls, pageErrors: errors };
  await writeFile(base + 'browser-repro.json', JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result, null, 2));
} finally { await context.close(); await browser.close(); }
