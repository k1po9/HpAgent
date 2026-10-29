import type { Page } from "@playwright/test";
import { workFixture } from "../src/components/tasks/taskFixtures";
import type { HpWork } from "../src/api/types";

/** HTTP-only fixture for the real App: no Store injection, backend, or Provider. */
export async function installTaskLobeFixture(page: Page) {
  const base = workFixture();
  const state = {
    work: workFixture({
      work_id: "lobe-task",
      title: "任务交互验收",
      requirement: {
        ...base.requirement,
        objective: "保留任务交互、草稿、权限和原操作重试。",
        timing: {
          schema_version: 1,
          kind: "daily",
          timezone: "Asia/Shanghai",
          local_time: "09:00",
        },
        deliverable_policy: { schema_version: 1, required: true, directory_id: "notes" },
      },
    }),
    writes: [] as {
      path: string;
      body: Record<string, unknown>;
      key: string | undefined;
      match: string | undefined;
    }[],
    reads: [] as string[],
    pending: null as null | Promise<void>,
    failBudget: false,
    dropNextWrite: false,
  };
  const receipts = new Map<string, HpWork>();
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) return route.abort("blockedbyclient");
    const path = url.pathname;
    if (!path.startsWith("/api/") && !path.startsWith("/auth/")) return route.continue();
    if (request.method() !== "GET") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.writes.push({
        path,
        body,
        key: request.headers()["idempotency-key"],
        match: request.headers()["if-match"],
      });
      const key = request.headers()["idempotency-key"];
      const receipt = key ? receipts.get(key) : undefined;
      if (receipt) return route.fulfill({ json: { work: receipt } });
      if (state.pending) await state.pending;
      if (path.endsWith("/budget") && state.failBudget) {
        state.failBudget = false;
        state.work = { ...state.work, row_version: state.work.row_version + 1 };
        return route.fulfill({
          status: 409,
          json: { error: { code: "work_version_conflict", message: "预算版本已更新" } },
        });
      }
      if (path.endsWith("/budget")) {
        state.work = {
          ...state.work,
          row_version: state.work.row_version + 1,
          budget: {
            ...state.work.budget!,
            version: state.work.budget!.version + 1,
            limits: { ...state.work.budget!.limits, ...(body.limits as Record<string, number>) },
          },
        };
      } else if (path.endsWith("/pause"))
        state.work = { ...state.work, status: "paused", row_version: state.work.row_version + 1 };
      else if (path.endsWith("/resume"))
        state.work = { ...state.work, status: "active", row_version: state.work.row_version + 1 };
      else if (path.endsWith("/stop"))
        state.work = { ...state.work, status: "stopped", row_version: state.work.row_version + 1 };
      else if (path.endsWith("/revisions"))
        state.work = {
          ...state.work,
          requirement: body.requirement as HpWork["requirement"],
          row_version: state.work.row_version + 1,
          current_requirement_revision: state.work.current_requirement_revision + 1,
        };
      else if (!path.endsWith("/resources"))
        return route.fulfill({
          status: 409,
          json: { error: { code: "fixture_read_only", message: "此夹具不支持该写入" } },
        });
      if (key) receipts.set(key, structuredClone(state.work));
      if (state.dropNextWrite) {
        state.dropNextWrite = false;
        return route.abort("failed");
      }
      return route.fulfill({ json: { work: state.work } });
    }
    state.reads.push(path);
    let json: unknown = { items: [], next_before: null, grants: [], has_more: false };
    if (path === "/api/v1/me")
      json = {
        account: {
          account_id: "task-ui-fixture",
          status: "active",
          created_at: "2026-10-01T00:00:00Z",
        },
        session: {},
        csrf_token: "synthetic-task-ui",
        identities: { web: { username: "task-ui-fixture" }, qq: { bound: false } },
        capabilities: { agent_strategies: ["react", "plan_and_execute"] },
      };
    else if (path === "/api/v1/works") json = { items: [state.work], next_before: null };
    else if (path === "/api/v1/works/lobe-task") json = { work: state.work };
    else if (path.endsWith("/events/stream"))
      return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    else if (path.startsWith("/api/v1/workspace"))
      json = {
        workspace_id: "task-ui-fixture",
        root_id: "root",
        nodes: [
          { node_id: "root", parent_id: null, kind: "directory", name: "" },
          { node_id: "notes", parent_id: "root", kind: "directory", name: "研究资料" },
        ],
      };
    return route.fulfill({ json });
  });
  return state;
}
