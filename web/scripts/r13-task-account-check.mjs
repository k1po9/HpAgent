/* global document, window, innerWidth */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { workFixture } from "../src/components/tasks/taskFixtures.ts";
import { writeFile, mkdir } from "node:fs/promises";
import { URL } from "node:url";
import { resolve } from "node:path";
import { auditContrast } from "./r13-material-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06"),
  dir = resolve(out, "screenshots/states");
await mkdir(dir, { recursive: true });
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const ctx = await b.newContext({
    viewport: { width: 1680, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
  }),
  p = await ctx.newPage();
const results = [],
  errors = [],
  metrics = [];
p.on("pageerror", (e) => errors.push(e.message));
async function settled() {
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.evaluate(() => document.fonts.ready);
}
async function shot(name, data) {
  const geometry = await p.evaluate(() => ({
    width: innerWidth,
    doc: document.documentElement.scrollWidth,
  }));
  expect(geometry.doc).toBeLessThanOrEqual(geometry.width);
  await p.screenshot({ path: resolve(dir, name + ".png") });
  metrics.push({ name, data, geometry, contrast: await auditContrast(p) });
}
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, pass: true });
  } catch (e) {
    results.push({ name, pass: false, error: e.message });
    await p.screenshot({ path: resolve(dir, "failure-" + results.length + ".png") });
    throw e;
  }
}
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  const me = await (await p.request.get("http://127.0.0.1:5294/api/v1/me")).json();
  await check(
    "Actual task creation editor retains dirty draft and nested Surface focus",
    async () => {
      await p.goto("http://127.0.0.1:5294/#/tasks");
      await settled();
      await p.getByRole("button", { name: "新建任务", exact: true }).click();
      const editor = p.getByRole("dialog", { name: "新建任务", exact: true });
      await editor.getByLabel("工作名称").fill("R1.3 分区看板验证草稿");
      await editor.getByLabel("目标 / 提醒内容").fill("保留草稿并核对静态布局");
      await shot("task-create-draft", "real production editor, no submission");
      await p.keyboard.press("Escape");
      await expect(p.getByRole("dialog", { name: "放弃未提交的任务草稿？" })).toBeVisible();
      await p.getByRole("button", { name: "继续编辑", exact: true }).click();
      await expect(editor.getByLabel("工作名称")).toHaveValue("R1.3 分区看板验证草稿");
      await p.keyboard.press("Escape");
      await p.getByRole("button", { name: "放弃草稿并关闭", exact: true }).click();
      await expect(p.getByRole("dialog")).toHaveCount(0);
    },
  );
  let fixtures = [
    workFixture({
      work_id: "r13-budget",
      title: "项目研究预算待补充",
      continuation: { kind: "blocked", reason: "budget_exhausted" },
      budget: { ...workFixture().budget, used: { model_total_tokens: 1000 } },
    }),
    workFixture({
      work_id: "r13-active",
      title: "整理本周项目资料",
      active_coordinator_run_id: "controlled-run",
    }),
    workFixture({
      work_id: "r13-paused",
      title: "已暂停资料任务",
      status: "paused",
      continuation: { kind: "blocked", reason: "delivery_resolved" },
    }),
    workFixture({
      work_id: "r13-plan",
      title: "周会材料提醒",
      continuation: { kind: "at_time", reason: "scheduled", due_at: "2027-01-01T09:00:00+08:00" },
      requirement: { ...workFixture().requirement, capability_key: "reminder" },
    }),
    workFixture({ work_id: "r13-ended", title: "需求纪要整理", status: "completed" }),
    workFixture({ work_id: "r13-incomplete", title: "状态待核实任务", continuation: undefined }),
  ];
  await p.route("**/api/v1/works**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/works" && route.request().method() === "GET")
      return route.fulfill({ json: { items: fixtures, next_before: null } });
    const id = path.split("/")[4],
      work = fixtures.find((w) => w.work_id === id);
    if (!work) return route.continue();
    if (path.endsWith("/events/stream"))
      return route.fulfill({ status: 503, body: "controlled feed unavailable" });
    if (path.endsWith("/resume")) {
      const next = {
        ...work,
        status: "active",
        active_coordinator_run_id: "resumed-run",
        continuation: { kind: "ready", reason: "accepted" },
        row_version: work.row_version + 1,
      };
      fixtures = fixtures.map((w) => (w.work_id === id ? next : w));
      return route.fulfill({ json: { work: next } });
    }
    if (path.endsWith("/runs")) return route.fulfill({ json: { items: [] } });
    return route.fulfill({ json: { work } });
  });
  await check(
    "Controlled four-state board, type filter and card/menu action availability",
    async () => {
      await p.goto("http://127.0.0.1:5294/#/tasks?bucket=attention&type=all");
      await p.reload();
      await settled();
      await expect(p.getByRole("article")).toHaveCount(6);
      await expect(p.locator("[data-task-lane]")).toHaveCount(4);
      await shot(
        "four-state-board",
        "controlled API snapshots on real production; not persisted data",
      );
      const guarded = p.getByRole("article", { name: "状态待核实任务" });
      await expect(guarded.locator(".hp-task-primary-action")).toHaveText("查看详情");
      await expect(guarded.getByRole("button", { name: "继续执行", exact: true })).toHaveCount(0);
      await p.getByRole("article", { name: "项目研究预算待补充" }).locator("summary").click();
      await shot("board-more-actions", "controlled snapshots / actual available actions");
      await p.getByLabel("任务类型").selectOption("reminder");
      await expect(p.getByRole("article")).toHaveCount(1);
      await p.getByLabel("任务类型").selectOption("all");
    },
  );
  await check(
    "Primary action DOM and keyboard focus survive a bucket-changing command",
    async () => {
      const card = p.getByRole("article", { name: "已暂停资料任务" }),
        button = card.locator(".hp-task-primary-action");
      await button.focus();
      await button.evaluate((el) => (window.r13Primary = el));
      await button.click();
      await expect(card).toContainText("正在执行");
      expect(
        await button.evaluate((el) => el === window.r13Primary && el === document.activeElement),
      ).toBe(true);
      await expect(card.locator("..").locator(".hp-task-context-note")).toBeVisible();
      expect(await card.evaluate((el) => el.closest("[data-task-lane]").dataset.taskLane)).toBe(
        "waiting",
      );
      await p.locator("#canvas-title").focus();
      await expect
        .poll(() => card.evaluate((el) => el.closest("[data-task-lane]").dataset.taskLane))
        .toBe("active");
      await shot(
        "board-focus-retention",
        "controlled successful resume / real request handler and DOM",
      );
    },
  );
  await check(
    "Selected task detail, mobile nested budget Surface and focus restoration",
    async () => {
      const card = p.getByRole("article", { name: "项目研究预算待补充" });
      await card.locator(".hp-task-title").click();
      await expect(p.locator(".hp-task-inspector")).toBeVisible();
      await p.setViewportSize({ width: 390, height: 844 });
      await p
        .locator(".hp-task-inspector")
        .getByRole("button", { name: "提高预算", exact: true })
        .click();
      const budget = p.getByRole("dialog", { name: "提高任务预算" });
      await expect(budget).toBeVisible();
      await budget.getByRole("spinbutton", { name: /模型 Token 上限/ }).fill("2000");
      await shot(
        "task-budget-nested-mobile",
        "controlled task snapshot, real nested Surface, no budget submission",
      );
      await p.keyboard.press("Escape");
      await expect(
        p.locator(".hp-task-inspector").getByRole("button", { name: "提高预算", exact: true }),
      ).toBeFocused();
      await p.keyboard.press("Escape");
      await p.setViewportSize({ width: 1680, height: 1000 });
    },
  );
  await p.unroute("**/api/v1/works**");
  await p.goto("http://127.0.0.1:5294/#/tasks");
  await p.reload();
  await settled();
  let completed = false;
  await p.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: completed
        ? {
            ...me,
            identities: {
              ...me.identities,
              qq: { bound: true, display_subject: "QQ 已绑定身份（受控验证）" },
            },
          }
        : me,
    }),
  );
  let mode = "pending",
    gate;
  await p.route("**/api/v1/identity-bindings/qq/challenges**", async (route) => {
    if (route.request().method() === "POST") {
      if (mode === "slow") await new Promise((r) => (gate = r));
      if (mode === "error")
        return route.fulfill({
          status: 503,
          json: {
            error: {
              code: "controlled_binding_unavailable",
              message: "受控故障：绑定服务暂不可用",
            },
          },
        });
      return route.fulfill({
        json: {
          challenge_id: "controlled-binding",
          code: "R13-TEST-ONLY",
          status: "pending",
          expires_at: "2027-01-01T00:00:00Z",
        },
      });
    }
    if (mode === "completed") completed = true;
    return route.fulfill({
      json: {
        challenge_id: "controlled-binding",
        status: mode === "completed" ? "completed" : mode === "expired" ? "expired" : "pending",
        expires_at: "2027-01-01T00:00:00Z",
      },
    });
  });
  await check(
    "QQ creation loading/failure/retry and expired challenge preserve account Surface",
    async () => {
      await p.getByRole("button", { name: "账户设置", exact: true }).click();
      mode = "slow";
      await p.getByRole("button", { name: "绑定 QQ", exact: true }).click();
      await expect(p.getByRole("button", { name: "生成中…", exact: true })).toBeDisabled();
      await shot("qq-loading", "controlled network delay; real QQ component");
      await expect.poll(() => typeof gate).toBe("function");
      mode = "error";
      gate();
      await expect(p.getByRole("alert")).toBeVisible();
      await shot("qq-failure", "controlled 503, no external QQ messaging");
      mode = "pending";
      await p.getByRole("button", { name: "绑定 QQ", exact: true }).click();
      await expect(p.getByText("绑定 R13-TEST-ONLY", { exact: true })).toBeVisible();
      mode = "expired";
      await expect(p.getByText("绑定码已失效，请重新生成。")).toBeVisible({ timeout: 8000 });
      await shot("qq-expired", "controlled expired challenge");
    },
  );
  await check(
    "QQ completed challenge refreshes bound identity and removes bind action",
    async () => {
      mode = "pending";
      await p.getByRole("button", { name: "重新生成", exact: true }).click();
      await expect(p.getByText("绑定 R13-TEST-ONLY")).toBeVisible();
      mode = "completed";
      await expect(p.getByText("QQ 已绑定身份（受控验证）")).toBeVisible({ timeout: 8000 });
      await expect(p.getByRole("button", { name: "重新生成", exact: true })).toHaveCount(0);
      await shot(
        "qq-bound",
        "controlled completed challenge + controlled me projection; no persisted binding",
      );
      await p.keyboard.press("Escape");
      await expect(p.getByRole("button", { name: "账户设置", exact: true })).toBeFocused();
    },
  );
  await p.unroute("**/api/v1/me");
  await p.unroute("**/api/v1/identity-bindings/qq/challenges**");
  await p.goto("http://127.0.0.1:5294/");
  await settled();
  await check("Actual account signout disposes session and returns to login", async () => {
    await p.getByRole("button", { name: "账户设置", exact: true }).click();
    await p.getByRole("button", { name: "退出登录", exact: true }).click();
    await expect(p.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
    await shot("actual-signout", "actual isolated review-session logout");
  });
  expect(errors).toEqual([]);
} finally {
  await writeFile(
    resolve(out, "logs/task-account-states.json"),
    JSON.stringify({ browser: await b.version(), results, metrics, errors }, null, 2),
  );
  await b.close();
}
