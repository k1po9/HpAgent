import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { login, createConversation, sendMessage, expectReply } from "./helpers";
import { workFixture, artifactFixture } from "../src/components/tasks/taskFixtures";
const evidence = resolve(process.cwd(), "../artifacts/product-acceptance/ui-6/screenshots");

test("real API: lost create/version responses replay keys and history keeps v1 selected", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "HTML 版本恢复验收");
  await expectReply(page);
  const createKeys: string[] = [],
    versionKeys: string[] = [];
  let loseCreate = true,
    loseVersion = true;
  await page.route("**/api/v1/messages/*/artifacts", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    createKeys.push(route.request().headers()["idempotency-key"]!);
    if (loseCreate) {
      loseCreate = false;
      await route.fetch();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
  await page.getByRole("button", { name: "恢复原 HTML 生成" }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30000 });
  expect(createKeys).toHaveLength(2);
  expect(createKeys[1]).toBe(createKeys[0]);
  const v1Url = page.url();
  await page.route("**/api/v1/artifacts/*/versions", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    versionKeys.push(route.request().headers()["idempotency-key"]!);
    expect(route.request().postDataJSON()).toEqual({ instruction: "修改标题并保留交互" });
    if (loseVersion) {
      loseVersion = false;
      await route.fetch();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.getByLabel("修改指令").fill("修改标题并保留交互");
  await page.getByRole("button", { name: "生成新版本", exact: true }).click();
  await expect(page.getByLabel("修改指令")).toHaveValue("修改标题并保留交互");
  await page.getByRole("button", { name: "恢复原修改" }).click();
  expect(versionKeys).toHaveLength(2);
  expect(versionKeys[1]).toBe(versionKeys[0]);
  await expect(page.getByLabel("修改指令")).toHaveValue("");
  await expect(page).toHaveURL(v1Url);
  await page.getByRole("tab", { name: "版本历史" }).click();
  const v2 = page
    .locator(".hp-artifact-history li")
    .filter({ has: page.getByRole("button", { name: "查看 v2", exact: true }) });
  await expect(v2).toContainText("生成完成", { timeout: 30000 });
  await expect(v2).toContainText("基准：v1");
  await mkdir(evidence, { recursive: true });
  await page.screenshot({
    path: resolve(evidence, "real-api-version-history.png"),
    fullPage: true,
  });
  await v2.getByRole("button", { name: "查看 v2", exact: true }).click();
  await page.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  await page.getByLabel("修改指令").fill("尚未提交的草稿");
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await page.getByRole("button", { name: /HTML · v2 生成完成/ }).click();
  await expect(page.getByLabel("修改指令")).toHaveValue("尚未提交的草稿");
  await page.getByRole("tab", { name: "详情" }).click();
  await page.reload();
  await expect(page.getByRole("tab", { name: "详情" })).toHaveAttribute("aria-selected", "true");
});

test("network fixtures: task original delivery, unavailable versions, layouts and layered focus", async ({
  page,
}) => {
  await login(page);
  await mkdir(evidence, { recursive: true });
  const artifact = {
    artifact_id: "ui6-artifact",
    title: "很长的中文标题用于验证换行与历史版本保护 / Long HTML artifact title ".repeat(3),
    kind: "html",
    conversation_id: null,
    source_message_id: null,
    created_at: "2026-10-08T00:00:00Z",
    updated_at: "2026-10-08T00:00:00Z",
  };
  const v1 = {
    artifact_id: artifact.artifact_id,
    artifact_version_id: "ui6-v1",
    version: 1,
    status: "completed",
    parent_version_id: null,
    instruction: null,
    html: "<button onclick=\"this.textContent='开启'\">切换状态</button>",
    failure: null,
    created_at: artifact.created_at,
    started_at: null,
    completed_at: artifact.created_at,
  };
  const v2 = {
    ...v1,
    artifact_version_id: "ui6-v2",
    version: 2,
    parent_version_id: "ui6-v1",
    instruction: "手工修改",
  };
  let items = [v2, v1];
  let denied = false;
  const work = workFixture({
    work_id: "ui6-task",
    artifacts: [
      {
        ...artifactFixture,
        artifact_id: artifact.artifact_id,
        artifact_version_id: v1.artifact_version_id,
      },
    ],
  });
  await page.route("**/api/v1/artifacts/ui6-artifact/versions", (route) =>
    denied
      ? route.fulfill({
          status: 403,
          json: {
            error: {
              code: "forbidden",
              message: "权限失效",
              request_id: null,
              retryable: false,
              details: {},
            },
          },
        })
      : route.fulfill({ json: { artifact, items } }),
  );
  await page.route("**/api/v1/works", (route) =>
    route.fulfill({ json: { items: [work], next_before: null } }),
  );
  await page.route("**/api/v1/works/ui6-task", (route) => route.fulfill({ json: { work } }));
  await page.route("**/api/v1/works/ui6-task/runs", (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  await page.route("**/api/v1/works/ui6-task/events/stream*", (route) =>
    route.fulfill({ status: 503, body: "fixture feed" }),
  );
  const deepLink =
    "/#/tasks?bucket=attention&type=all&work=ui6-task&inspect=artifact:ui6-artifact&version=ui6-v2&tab=preview";
  await page.goto(deepLink);
  await expect(page.getByText(/任务原引用 · r2 · v1/)).toBeVisible();
  await expect(page.getByText("此版本由后续修改生成，不自动替代任务交付。")).toBeVisible();
  expect(await page.getByTitle("Artifact 预览").getAttribute("sandbox")).toBe("allow-scripts");
  for (const [width, height] of [
    [360, 800],
    [390, 844],
    [768, 1024],
    [1024, 768],
    [1280, 800],
    [1440, 900],
    [1920, 1080],
  ]) {
    await page.setViewportSize({ width, height });
    await expect(page.getByLabel("修改指令")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({
      path: resolve(evidence, `artifact-${width}x${height}.png`),
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.screenshot({ path: resolve(evidence, "artifact-reduced-motion.png"), fullPage: true });
  await page.getByRole("button", { name: "保存源码副本到空间" }).click();
  const save = page.getByRole("dialog", { name: "保存到长期文件" });
  await expect(save).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(save).not.toBeVisible();
  await expect(page.locator(".hp-artifact-inspector")).toBeVisible();
  await expect(page.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
  await page.getByRole("button", { name: "返回任务成果与验收" }).click();
  await page.getByRole("button", { name: "查看原引用版本" }).click();
  await expect(page.getByText(/正在查看 v1/)).toBeVisible();
  await page.goto(deepLink.replace("version=ui6-v2", "version=missing"));
  await expect(page.getByText(/指定版本不可用/)).toBeVisible();
  await expect(page.getByTitle("Artifact 预览")).toHaveCount(0);
  await page.screenshot({ path: resolve(evidence, "artifact-unavailable.png"), fullPage: true });
  items = [
    { ...v2, status: "failed", failure: { code: "fixture_build_failed", message: "合成构建失败" } },
    v1,
  ];
  await page.goto(deepLink);
  await page.reload();
  await expect(page.getByText("合成构建失败")).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "artifact-build-failed.png"), fullPage: true });
  items = [];
  await page.reload();
  await expect(page.getByText(/暂无版本内容/)).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "artifact-empty.png"), fullPage: true });
  denied = true;
  await page.reload();
  await expect(page.getByText("对象不可用。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("修改指令")).toHaveCount(0);
  await page.screenshot({ path: resolve(evidence, "artifact-denied.png"), fullPage: true });
});

test("real API upload with injected lost PUT and name conflict: source copy recovers without reupload", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "源码副本恢复验收");
  await expectReply(page);
  await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30000 });
  let uploads = 0,
    puts = 0,
    saves = 0,
    grants = 0,
    accepts = 0;
  await page.route("**/api/v1/uploads/*/content", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    puts++;
    await route.fetch();
    return route.abort("failed");
  });
  await page.route("**/api/v1/workspace/files", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    saves++;
    if (saves === 1)
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "workspace_name_conflict",
            message: "合成同名冲突",
            request_id: null,
            retryable: false,
            details: {},
          },
        },
      });
    return route.continue();
  });
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/workspace/uploads") {
      uploads++;
      expect(request.postDataJSON().content_type).toBe("text/plain");
    }
    if (path.endsWith("/resources")) grants++;
    if (path.endsWith("/accept-result")) accepts++;
  });
  await page.getByRole("button", { name: "保存源码副本到空间" }).click();
  const dialog = page.getByRole("dialog", { name: "保存到长期文件" });
  await expect(dialog.getByLabel("保存名称")).toHaveValue(/-v1\.html\.txt$/);
  await dialog.getByRole("button", { name: "确认保存" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: resolve(evidence, "source-save-lost-put.png"), fullPage: true });
  await dialog.getByRole("button", { name: "继续原保存" }).click();
  await expect(dialog.getByRole("alert")).toContainText("合成同名冲突");
  await dialog.getByLabel("保存名称").fill(`源码副本-${Date.now()}-v1.html.txt`);
  await dialog.getByRole("button", { name: "继续原保存" }).click();
  await expect(dialog).not.toBeVisible();
  expect(uploads).toBe(1);
  expect(puts).toBe(1);
  expect(saves).toBe(2);
  expect(grants).toBe(0);
  expect(accepts).toBe(0);
  await expect(page.getByRole("status").filter({ hasText: "源码副本已保存到空间" })).toBeVisible();
  await page.getByRole("button", { name: "打开空间", exact: true }).click();
  await expect(page).toHaveURL(/#\/workspace/);
  await page.screenshot({ path: resolve(evidence, "source-save-space-link.png"), fullPage: true });
});

test("network fixtures: loading and query failure recover to the default successful version", async ({
  page,
}) => {
  await login(page);
  await mkdir(evidence, { recursive: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = true;
  const artifact = {
    artifact_id: "ui6-query",
    title: "查询恢复 HTML",
    kind: "html",
    conversation_id: null,
    source_message_id: null,
    created_at: "2026-10-08",
    updated_at: "2026-10-08",
  };
  const version = {
    artifact_id: artifact.artifact_id,
    artifact_version_id: "ui6-ready",
    version: 7,
    status: "completed",
    parent_version_id: null,
    instruction: null,
    html: "<p>成功版 v7</p>",
    failure: null,
    created_at: artifact.created_at,
    started_at: null,
    completed_at: artifact.created_at,
  };
  await page.route("**/api/v1/artifacts/ui6-query/versions", async (route) => {
    await gate;
    return fail
      ? route.fulfill({
          status: 503,
          json: {
            error: {
              code: "service_unavailable",
              message: "合成查询中断",
              request_id: "fixture-request",
              retryable: true,
              details: {},
            },
          },
        })
      : route.fulfill({ json: { artifact, items: [version] } });
  });
  await page.goto("/#/ai?inspect=artifact:ui6-query&tab=preview");
  await expect(page.getByText("正在加载 HTML 成果…")).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "artifact-loading.png"), fullPage: true });
  release();
  await expect(page.getByText("合成查询中断")).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "artifact-query-error.png"), fullPage: true });
  fail = false;
  await page.locator(".hp-inspector").getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  await expect(page).toHaveURL(/version=ui6-ready/);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("修改指令").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: resolve(evidence, "artifact-mobile-composer.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await expect(page.getByRole("textbox", { name: "消息输入" })).toBeEnabled();
  await page.screenshot({ path: resolve(evidence, "artifact-closed.png"), fullPage: true });
});
