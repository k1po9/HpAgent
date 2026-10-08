import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { login, createConversation, sendMessage, expectReply } from "./helpers";
import { workFixture } from "../src/components/tasks/taskFixtures";
test.setTimeout(120_000);
const evidence = resolve(process.cwd(), "../artifacts/product-acceptance/ui-7/screenshots");
const sizes = [
  [360, 800],
  [390, 844],
  [768, 1024],
  [1024, 768],
  [1280, 800],
  [1440, 900],
  [1920, 1080],
];
const artifact = {
  artifact_id: "ui7-artifact",
  title: "很长的中文标题和英文路径 / LongTitleWithoutSpaces".repeat(4),
  kind: "html",
  conversation_id: null,
  source_message_id: null,
  created_at: "2026-10-08T00:00:00Z",
  updated_at: "2026-10-08T00:00:00Z",
};
const version = {
  artifact_id: artifact.artifact_id,
  artifact_version_id: "ui7-v1",
  version: 1,
  status: "completed",
  parent_version_id: null,
  instruction: null,
  html: "<button onclick=\"this.textContent='保留 iframe 状态'\">改变预览状态</button>",
  failure: null,
  created_at: artifact.created_at,
  started_at: null,
  completed_at: artifact.created_at,
};
const snapshot = {
  source_kind: "work",
  run: {
    source_kind: "work",
    run_id: "ui7-run",
    execution_id: "execution",
    work_id: "ui7-task",
    requirement_revision: 2,
    work_control_epoch: 1,
    conversation_id: null,
    session_id: null,
    status: "succeeded",
    version: 2,
    created_at: artifact.created_at,
    started_at: null,
    finished_at: artifact.created_at,
    updated_at: artifact.created_at,
    failure_code: null,
    failure_message: null,
    strategy_kind: "generic_agent",
    executor_key: "general",
    result_json: { summary: "真实投影结构的测试夹具" },
    budget: null,
    branches: [],
  },
};
async function fixtures(page: Page) {
  let artifactReads = 0;
  await page.route("**/api/v1/artifacts/ui7-artifact/versions", async (route) => {
    artifactReads++;
    await route.fulfill({ json: { artifact, items: [version] } });
  });
  await page.route("**/api/v1/runs/ui7-run", (route) => route.fulfill({ json: snapshot }));
  await page.route("**/api/v1/runs/ui7-run/trace", (route) =>
    route.fulfill({ json: { run: null, roots: [] } }),
  );
  await page.route("**/api/v1/runs/ui7-run/model-inputs", (route) =>
    route.fulfill({ json: { visibility: "none", items: [] } }),
  );
  await page.route("**/api/v1/works/ui7-task", (route) =>
    route.fulfill({ json: { work: workFixture({ work_id: "ui7-task", title: artifact.title }) } }),
  );
  return () => artifactReads;
}
async function geometry(page: Page) {
  const value = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
    canvas: document.querySelector(".hp-main-canvas")?.getBoundingClientRect().width,
    sidebar: document.querySelector(".hp-context-sidebar")?.getBoundingClientRect().width,
  }));
  expect(value.document).toBeLessThanOrEqual(value.viewport + 1);
  if (value.viewport >= 1280) expect(value.canvas).toBeGreaterThanOrEqual(560);
  const close = page.locator(".hp-inspector .hp-surface-header button").last();
  if (await close.isVisible()) {
    const bounds = (await close.boundingBox())!;
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(value.viewport + 1);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
  }
  return value;
}
async function screenshot(page: Page, name: string) {
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: resolve(evidence, name + ".png"), fullPage: true });
}

test("seven viewports and boundary resizes keep Artifact iframe, draft and query count", async ({
  page,
}) => {
  await login(page);
  const reads = await fixtures(page);
  await page.goto("/#/ai?inspect=artifact:ui7-artifact&version=ui7-v1");
  await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  await page
    .frameLocator('iframe[title="Artifact 预览"]')
    .getByRole("button", { name: "改变预览状态" })
    .click();
  await page.getByLabel("修改指令").fill("保留断点草稿");
  const initial = reads();
  const measurements = [];
  for (const [width, height] of [
    ...sizes,
    [1279, 800],
    [1280, 800],
    [959, 800],
    [960, 800],
    [599, 800],
    [600, 800],
  ]) {
    await page.setViewportSize({ width: width!, height: height! });
    await expect(page.getByLabel("修改指令")).toHaveValue("保留断点草稿");
    await expect(
      page
        .frameLocator('iframe[title="Artifact 预览"]')
        .getByRole("button", { name: "保留 iframe 状态" }),
    ).toBeVisible();
    measurements.push(await geometry(page));
    expect(reads()).toBe(initial);
    if (sizes.some(([w, h]) => w === width && h === height))
      await screenshot(page, `artifact-${width}x${height}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "扩大阅读" }).click();
  await geometry(page);
  expect(reads()).toBe(initial);
  await screenshot(page, "artifact-expanded");
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(
    await page
      .locator(".hp-inspector")
      .evaluate((n) => n.getAnimations().filter((a) => a.playState === "running").length),
  ).toBe(0);
  await screenshot(page, "artifact-reduced-motion");
  await page.addStyleTag({ content: "html { font-size: 32px !important; }" });
  await geometry(page);
  await screenshot(page, "artifact-text-200-percent");
  await writeFile(
    resolve(evidence, "geometry.json"),
    JSON.stringify(
      { initialArtifactReads: initial, finalArtifactReads: reads(), measurements },
      null,
      2,
    ),
  );
});

test("mobile saving suspends the parent; Escape restores draft, iframe and focus", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const reads = await fixtures(page);
  await page.goto("/#/ai?inspect=artifact:ui7-artifact&version=ui7-v1");
  await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  await page.getByLabel("修改指令").fill("保存覆盖期间保留");
  const initial = reads();
  await page.getByRole("button", { name: "保存源码副本到空间" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(page.locator(".hp-inspector")).toHaveAttribute("hidden", "");
  await screenshot(page, "mobile-save-layer");
  await page.keyboard.press("Escape");
  await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  await expect(page.getByLabel("修改指令")).toHaveValue("保存覆盖期间保留");
  await expect(page.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
  expect(reads()).toBe(initial);
  await page.keyboard.press("Escape");
  await expect(page.locator(".hp-inspector")).toHaveCount(0);
  await expect(page.locator("#canvas-title")).toBeFocused();
});

test("all four Inspectors have keyboard tabs, URL replace, geometry and screenshots", async ({
  page,
}) => {
  await login(page);
  await fixtures(page);
  const tree = await (await page.request.get("/api/v1/workspace")).json();
  // The real account's root directory provides File context without fabricated file content.
  const rootId = tree.root_id;
  for (const [kind, id, screen] of [
    ["artifact", "ui7-artifact", "ai"],
    ["run", "ui7-run", "ai"],
    ["task", "ui7-task", "tasks"],
    ["file", rootId, "workspace"],
  ]) {
    await page.goto(`/#/${screen}?inspect=${kind}:${id}`);
    await expect(page.locator(".hp-inspector [role=tablist]")).toBeVisible();
    const tabs = page.locator(".hp-inspector [role=tab]");
    await tabs.first().focus();
    await page.keyboard.press("End");
    await expect(tabs.last()).toBeFocused();
    await expect(tabs.last()).toHaveAttribute("aria-selected", "true");
    await expect(page).toHaveURL(/tab=/);
    await page.keyboard.press("Home");
    await expect(tabs.first()).toBeFocused();
    for (const [width, height] of sizes) {
      await page.setViewportSize({ width: width!, height: height! });
      await geometry(page);
      await screenshot(page, `${kind}-${width}x${height}-tabs`);
    }
    await page
      .getByRole("button", { name: /^关闭(执行详情|任务详情|目录详情|HTML 成果)$/ })
      .click();
    await expect(page.locator(".hp-inspector")).toHaveCount(0);
  }
});

test("formal Header creation keeps instruction and verified Run lookup handles denial", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "UI-7 创建与查询验收");
  await expectReply(page);
  await page.getByRole("button", { name: "从回复生成 HTML" }).click();
  await page.getByLabel("来源消息").selectOption({ index: 1 });
  await page.getByLabel("生成要求").fill("用中文生成报告");
  const created = page.waitForResponse(
    (r) =>
      /\/messages\/[^/]+\/artifacts$/.test(new URL(r.url()).pathname) &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建成果", exact: true }).click();
  expect((await created).request().postDataJSON()).toEqual({ instruction: "用中文生成报告" });
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await page.getByText("按执行编号查询", { exact: true }).click();
  await page.getByLabel("执行编号").fill("00000000-0000-0000-0000-000000000007");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.getByText("对象不可用。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("执行编号")).toHaveValue("00000000-0000-0000-0000-000000000007");
  await screenshot(page, "lookup-unavailable");
});

test("nested Task abandonment and inbox keep the parent; keyboard and color pairs remain usable", async ({
  page,
}) => {
  await login(page);
  await fixtures(page);
  await page.goto("/#/tasks?inspect=task:ui7-task");
  await expect(page.locator(".hp-task-inspector")).toBeVisible();
  await page.locator(".hp-task-inspector").evaluate((node) => {
    node.setAttribute("data-lifecycle-sentinel", "same");
  });
  await page.getByRole("button", { name: "收件箱", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(page.locator(".hp-inspector")).toHaveAttribute("hidden", "");
  await screenshot(page, "mobile-inbox-layer");
  await page.keyboard.press("Escape");
  await expect(page.locator(".hp-task-inspector")).toHaveAttribute(
    "data-lifecycle-sentinel",
    "same",
  );
  await page.getByRole("button", { name: "修改要求", exact: true }).click();
  await page
    .getByRole("dialog", { name: "修改任务要求", exact: true })
    .getByRole("textbox", { name: "目标 / 提醒内容", exact: true })
    .fill("保留键盘草稿");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "放弃未提交的任务草稿？" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(
    page
      .getByRole("dialog", { name: "修改任务要求", exact: true })
      .getByRole("textbox", { name: "目标 / 提醒内容", exact: true }),
  ).toHaveValue("保留键盘草稿");
  await page.keyboard.press("Tab");
  const focus = await page.evaluate(() =>
    document.activeElement?.closest("dialog")?.getAttribute("aria-label"),
  );
  expect(focus).toBe("修改任务要求");
  await page.getByRole("button", { name: "关闭修改任务要求" }).click();
  await page.getByRole("button", { name: "放弃草稿并关闭" }).click();
  await expect(page.locator(".hp-task-inspector")).toHaveAttribute(
    "data-lifecycle-sentinel",
    "same",
  );
  const ratios = await page.locator('.hp-inspector [role="tab"]').evaluateAll((buttons) => {
    const parse = (value: string) => (value.match(/[\d.]+/g) ?? []).map(Number);
    const luminance = (values: number[]) =>
      values
        .slice(0, 3)
        .map((v) => {
          const c = v / 255;
          return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
        })
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i]!, 0);
    return buttons.map((button) => {
      const foreground = getComputedStyle(button).color;
      let node: Element | null = button;
      let background = "rgb(255, 255, 255)";
      while (node) {
        const color = getComputedStyle(node).backgroundColor;
        const rgb = parse(color);
        if (rgb.length === 3 || rgb[3] === 1) {
          background = color;
          break;
        }
        node = node.parentElement;
      }
      const a = luminance(parse(foreground)),
        b = luminance(parse(background));
      return {
        text: button.textContent,
        foreground,
        background,
        ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      };
    });
  });
  for (const pair of ratios) expect(pair.ratio).toBeGreaterThanOrEqual(4.5);
  const targets = await page
    .locator('.hp-inspector .hp-surface-header button, .hp-inspector [role="tab"]')
    .evaluateAll((buttons) =>
      buttons.map((n) => ({
        name: n.textContent,
        width: n.getBoundingClientRect().width,
        height: n.getBoundingClientRect().height,
      })),
    );
  for (const target of targets) {
    expect(target.width).toBeGreaterThanOrEqual(44);
    expect(target.height).toBeGreaterThanOrEqual(44);
  }
  await writeFile(
    resolve(evidence, "a11y-measurements.json"),
    JSON.stringify({ ratios, targets }, null, 2),
  );
  await screenshot(page, "task-keyboard-mobile");
});

test("explicit login assembles once; restore skips it and send/stop keep one fixed control", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const stats = { added: 0 };
    Object.defineProperty(window, "__ui7Assembly", { value: stats });
    new MutationObserver((records) => {
      for (const record of records)
        for (const node of record.addedNodes) {
          if (node instanceof Element)
            stats.added +=
              Number(node.matches(".hp-assembly")) + node.querySelectorAll(".hp-assembly").length;
        }
    }).observe(document, { childList: true, subtree: true });
  });
  await login(page);
  expect(
    await page.evaluate(
      () => (window as unknown as { __ui7Assembly: { added: number } }).__ui7Assembly.added,
    ),
  ).toBe(1);
  await expect(page.locator(".hp-assembly")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".hp-shell")).toBeVisible();
  expect(
    await page.evaluate(
      () => (window as unknown as { __ui7Assembly: { added: number } }).__ui7Assembly.added,
    ),
  ).toBe(0);
  await createConversation(page);
  const action = page.locator(".hp-composer__action");
  await action.evaluate((node) => node.setAttribute("data-sentinel", "same-action"));
  const before = (await action.boundingBox())!;
  await page.getByPlaceholder("输入消息，Enter 发送").fill("UI7 固定发送停止控件");
  await action.click();
  await expect(action).toHaveAttribute("data-state", "stop");
  await expect(action).toHaveAttribute("data-sentinel", "same-action");
  const during = (await action.boundingBox())!;
  expect(during.width).toBeCloseTo(before.width, 0);
  expect(during.height).toBeCloseTo(before.height, 0);
  await action.click();
  await expect(action).toHaveAttribute("data-state", "send");
  await expect(action).toHaveAttribute("data-sentinel", "same-action");
  const after = (await action.boundingBox())!;
  expect(after.width).toBeCloseTo(before.width, 0);
  expect(after.height).toBeCloseTo(before.height, 0);
  await mkdir(evidence, { recursive: true });
  await writeFile(
    resolve(evidence, "motion-controls.json"),
    JSON.stringify({ before, during, after }, null, 2),
  );
});
