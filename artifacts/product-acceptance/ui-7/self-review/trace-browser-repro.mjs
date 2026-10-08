import { chromium, expect } from '/home/hp/workspace/HpAgent_web/web/node_modules/@playwright/test/index.mjs';
import { writeFile } from 'node:fs/promises';
const base = new URL('.', import.meta.url).pathname;
const browser = await chromium.launch();
const page = await browser.newPage();
const calls = [], errors = [];
page.on('pageerror', e => errors.push(e.message));
const now = '2026-10-08T00:00:00Z';
const conv = { conversation_id: 'c1', title: '普通聊天', status: 'active', last_message_seq: 2, metadata_version: 1, created_at: now, updated_at: now };
const run = { run_id: 'chat-run', conversation_id: 'c1', session_id: 's1', trigger_message_id: 'user', retry_of_run_id: null, status: 'running', failure: null, version: 1, created_at: now, started_at: now, finished_at: null, updated_at: now, budget: null, agent_strategy: 'react' };
const message = { message_id: 'assistant', conversation_id: 'c1', role: 'assistant', status: 'pending', content: null, sequence: 2, client_request_id: null, produced_by_run_id: run.run_id, created_at: now, completed_at: null };
const terminal = { source_kind: 'chat', run: { ...run, status: 'succeeded', finished_at: now, version: 2 }, assistant_message: { ...message, status: 'completed', content: '普通聊天完成回复', completed_at: now } };
await page.route('**/api/v1/**', async route => {
  const path = new URL(route.request().url()).pathname;
  calls.push({ path, method: route.request().method() });
  let json = { items: [], next_cursor: null, has_more: false };
  if (path === '/api/v1/me') json = { account: { account_id: 'review', status: 'active', created_at: now }, session: {}, csrf_token: 'fixture', identities: { web: { username: 'review' }, qq: { bound: false } }, capabilities: {} };
  if (path === '/api/v1/conversations') json = { ...json, items: [conv] };
  if (path === '/api/v1/conversations/c1') json = { conversation: conv, active_run: { run, assistant_message: message } };
  if (path === '/api/v1/conversations/c1/messages') json = { items: [message], next_cursor: null, has_more: false, conversation_last_message_seq: 2 };
  if (path.endsWith('/resources')) json = { grants: [], attachments: [], count: 0, next: null, candidates: [] };
  if (path === '/api/v1/runs/chat-run') json = terminal;
  if (path === '/api/v1/runs/chat-run/trace') json = { run: null, roots: [] };
  if (path === '/api/v1/runs/chat-run/events') {
    const payload = { schema_version: 1, event_id: 'terminal', event_type: 'run.succeeded', conversation_id: 'c1', run_id: 'chat-run', message_id: 'assistant', stream_id: null, event_seq: null, occurred_at: now, payload: { snapshot: terminal } };
    return route.fulfill({ contentType: 'text/event-stream', body: 'id: terminal\nevent: run.succeeded\ndata: ' + JSON.stringify(payload) + '\n\n' });
  }
  await route.fulfill({ json });
});
try {
  await page.goto('http://127.0.0.1:5288/#/ai/c1');
  await expect(page.getByText('普通聊天完成回复', { exact: true })).toBeVisible();
  await expect.poll(() => calls.filter(c => c.path.endsWith('/trace')).length).toBeGreaterThan(0);
  const result = { scope: 'Chromium with response fixtures and synthetic SSE terminal; no real API/backend', inspectorVisible: await page.locator('.hp-inspector').count() > 0, expectedTraceRequestsWithoutDiagnostics: 0, actualTraceRequests: calls.filter(c => c.path.endsWith('/trace')).length, calls, pageErrors: errors };
  await page.screenshot({ path: base + 'R3-ordinary-chat.png' });
  await writeFile(base + 'trace-browser-repro.json', JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result, null, 2));
} finally { await browser.close(); }
