/* global window, document, location, innerWidth, getComputedStyle, URLSearchParams, console, process */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { mkdir, writeFile } from "node:fs/promises";
const out = resolve(
  "artifacts/product-acceptance/ui-iteration/R4/attempt-13-inspector-visual-migration",
);
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const conversation = "01a13187-fc64-7b62-bc70-bc5a6d25c0ad";
const reports = [],
  errors = [],
  network = [];
let pages = [];
await mkdir(out + "/screenshots/interactions", { recursive: true });
async function scene(
  kind = "run:r4-A",
  tab = "overview",
  query = "",
  width = 1680,
  motion = "reduce",
  stage = "after",
) {
  const p = await browser.newPage({
    viewport: { width, height: 1000 },
    reducedMotion: motion,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  });
  pages.push(p);
  p.setDefaultTimeout(7000);
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("request", (r) => {
    if (/^https?:/.test(r.url())) network.push(r.url());
  });
  const file =
    stage === "target"
      ? "/inputs/HpAgent_Inspector_R2_Visual_System_Integrated_v1.1.html"
      : "/offline/AFTER.React.Standalone.html";
  const params = new URLSearchParams({ inspect: kind, tab });
  if (kind.startsWith("artifact:")) params.set("version", "r42-B-v2");
  await p.goto(
    pathToFileURL(out + file).href + (query ? "?" + query : "") + `#/ai/${conversation}?${params}`,
  );
  await p.waitForFunction(() => window.__handoffBooted);
  await expect(p.locator(".hp-inspector")).toBeVisible();
  return p;
}
async function check(name, fn) {
  pages = [];
  try {
    const evidence = await fn();
    reports.push({ name, result: "pass", evidence });
    console.log("PASS", name);
  } catch (e) {
    reports.push({ name, result: "fail", error: String(e) });
    console.log("FAIL", name, String(e));
  } finally {
    for (const [i, p] of pages.entries()) {
      await p
        .screenshot({ path: `${out}/screenshots/interactions/${name}-${i}.png` })
        .catch(() => {});
      await writeFile(
        `${out}/evidence/${name}-${i}.json`,
        JSON.stringify(
          await p
            .evaluate(() => ({
              hash: location.hash,
              focus: {
                tag: document.activeElement?.tagName,
                id: document.activeElement?.id,
                text: document.activeElement?.textContent?.slice(0, 100),
              },
              requests: window.__handoffLog,
              events: window.__r4events,
              timeline: window.__r4log,
              hiddenLeaks: [...document.querySelectorAll(".hp-inspector [hidden],dialog[hidden]")]
                .filter((n) => getComputedStyle(n).display !== "none")
                .map((n) => n.className),
            }))
            .catch(() => ({})),
          null,
          2,
        ),
      );
      await p.close();
    }
    await writeFile(
      out + "/evidence/interaction-regression.json",
      JSON.stringify(
        { browser: browser.version(), reports, errors, externalRequests: network },
        null,
        2,
      ),
    );
  }
}
const shot = (p, name) => p.screenshot({ path: `${out}/screenshots/interactions/${name}.png` });
await check("artifact-identity-version-draft", async () => {
  const p = await scene("artifact:r42-B", "preview");
  const frame = p.frameLocator('iframe[title="Artifact 预览"]');
  await frame.locator("#counter").click();
  await p.getByLabel("修改指令").fill("保留迁回后的草稿");
  await p.evaluate(() => {
    window.savedFrame = document.querySelector("iframe");
    window.savedEditor = document.querySelector("textarea");
    window.savedCapture = document.querySelector(".hp-capture-dual");
  });
  for (const tab of ["详情", "版本历史", "预览"])
    await p.getByRole("tab", { name: tab, exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  expect(
    await p.evaluate(
      () =>
        window.savedFrame === document.querySelector("iframe") &&
        window.savedEditor === document.querySelector("textarea") &&
        window.savedCapture === document.querySelector(".hp-capture-dual"),
    ),
  ).toBe(true);
  await expect(frame.locator("#counter")).toHaveText("1");
  await expect(p.getByLabel("修改指令")).toHaveValue("保留迁回后的草稿");
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await p.getByRole("button", { name: "查看 v1", exact: true }).click();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(frame.locator("#version")).toHaveText("r42-B · v1");
  expect(
    await p.evaluate(
      () =>
        window.savedFrame !== document.querySelector("iframe") &&
        window.savedEditor === document.querySelector("textarea"),
    ),
  ).toBe(true);
  await p.getByRole("button", { name: "生成新版本", exact: true }).click();
  await expect(p.getByRole("alert")).toContainText("离线交接");
});
await check("trace-budget-modelinput", async () => {
  const p = await scene("run:r4-A", "advanced");
  await p.getByText("预算与模型用量", { exact: true }).click();
  await expect(p.getByTestId("run-token-usage")).toContainText("6.4");
  await p
    .getByRole("button", { name: "折叠节点", exact: true })
    .filter({ visible: true })
    .first()
    .click();
  await p.getByRole("button", { name: "展开节点", exact: true }).first().click();
  await p.locator(".hp-trace-tree__node").filter({ hasText: "阅读资料并生成报告" }).click();
  await p.getByRole("button", { name: "查看 Model Input", exact: true }).click();
  await expect(
    p.locator('[aria-label="Trace 节点详情"]').getByTestId("model-input-provider-body"),
  ).toContainText("明确离线夹具");
  await p.getByRole("button", { name: "查看模型记录 model-fi" }).click();
  await expect(p.getByTestId("model-input-full_safe")).toHaveCount(2);
});
await check("run-file-task-parent-back", async () => {
  const p = await scene("run:r4-A", "resources");
  await p.getByRole("button", { name: "项目资料/项目评审说明.md", exact: true }).click();
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await p.getByRole("button", { name: "查看来源任务", exact: true }).click();
  await expect(p.locator(".hp-task-inspector")).toBeVisible();
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await expect(p.getByRole("tab", { name: "详情", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await expect(p.getByRole("tab", { name: "使用资料与输出", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
await check("file-permissions-dirty-guard", async () => {
  const p = await scene("file:ai-output-node", "usage");
  await p.getByLabel(/^目标对话/).selectOption(conversation);
  await p.getByLabel("读取内容", { exact: true }).uncheck();
  const hash = await p.evaluate(() => location.hash);
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  const guard = p.getByRole("dialog", { name: "放弃未提交的权限编辑？", exact: true });
  await expect(guard).toBeVisible();
  expect(await p.evaluate(() => location.hash)).toBe(hash);
  await guard.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(p.getByLabel("读取内容", { exact: true })).not.toBeChecked();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await guard.getByRole("button", { name: "放弃编辑并继续", exact: true }).click();
  await expect(p.locator(".hp-file-preview")).toBeVisible();
});
await check("task-original-version-and-report", async () => {
  const p = await scene("task:ai-task", "outputs");
  await p.getByRole("button", { name: "读取报告与输出文件", exact: true }).click();
  await expect(p.locator(".hp-task-report")).toContainText("项目评审说明");
  await shot(p, "task-report-open");
  await p.getByRole("button", { name: "查看原引用版本", exact: true }).click();
  await expect(p.frameLocator('iframe[title="Artifact 预览"]').locator("#version")).toHaveText(
    "r42-B · v1",
  );
  await expect(p.locator(".hp-artifact-origin")).toContainText("任务原引用");
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await expect(p.getByRole("tab", { name: "成果与执行", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
for (const stage of ["target", "after"]) {
  for (const [name, kind, tab, button, title] of [
    ["save", "run:r4-A", "resources", "保存到空间", "保存到长期文件"],
    ["artifact-save", "artifact:r42-B", "preview", "保存源码副本到空间", "保存到长期文件"],
    ["file-mutation", "file:ai-output-node", "preview", "改名、移动或移除入口", "管理空间入口"],
    ["file-use", "file:ai-output-node", "preview", "在对话中使用", "在对话中使用资料"],
    ["task-edit", "task:ai-task", "overview", "修改要求", "修改任务要求"],
    ["task-budget", "task:ai-task", "overview", "提高预算", "提高任务预算"],
    ["task-stop", "task:ai-task", "overview", "停止任务", "停止任务"],
    ["task-link", "task:ai-task", "overview", "关联对话", "关联任务到对话"],
    ["task-accept", "task:ai-task", "outputs", "接受这份成果", "接受指定成果版本"],
    ["task-delivery", "task:ai-task", "outputs", "确认已送达", "确认投递决策"],
    ["task-target", "task:ai-task", "advanced", "停用此通知目标", "停用通知目标"],
  ])
    await check(`${stage}-dialog-${name}`, async () => {
      const p = await scene(kind, tab, "", 1680, "reduce", stage);
      const trigger = p.getByRole("button", { name: button, exact: true });
      await trigger.click();
      const dialog = p.getByRole("dialog", { name: title, exact: true });
      await expect(dialog).toBeVisible();
      await expect(p.locator("#inspector-title")).toHaveCount(1);
      expect(await dialog.evaluate((n) => n.contains(document.activeElement))).toBe(true);
      await shot(p, `${stage}-dialog-${name}-open`);
      await p.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(p.locator(".hp-inspector")).toBeVisible();
      await expect(trigger).toBeFocused();
    });
}
await check("task-edit-nested-guard", async () => {
  const p = await scene("task:ai-task", "overview", "", 390);
  await p.getByRole("button", { name: "修改要求", exact: true }).click();
  const editor = p.getByRole("dialog", { name: "修改任务要求", exact: true });
  const field = editor.locator("textarea").first();
  await field.fill("保留这份尚未提交的任务要求");
  await p.keyboard.press("Escape");
  const guard = p.getByRole("dialog", { name: "放弃未提交的任务草稿？", exact: true });
  await expect(guard).toBeVisible();
  await expect(p.locator("dialog[open]:not(.hp-surface--region)")).toHaveCount(1);
  await shot(p, "task-nested-guard-mobile");
  await p.keyboard.press("Escape");
  await expect(editor).toBeVisible();
  await expect(field).toHaveValue("保留这份尚未提交的任务要求");
  await p.keyboard.press("Escape");
  await guard.getByRole("button", { name: "放弃草稿并关闭", exact: true }).click();
  await expect(p.locator(".hp-inspector")).toBeVisible();
  await expect(p.getByRole("button", { name: "修改要求", exact: true })).toBeFocused();
});
await check("file-lineage-version-form", async () => {
  const p = await scene("file:ai-output-node", "details");
  await p.getByRole("button", { name: "查看来源链", exact: true }).click();
  await expect(p.locator(".hp-file-inspector")).toContainText("交付报告-v1.md");
  await shot(p, "file-lineage-open");
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await expect(p.getByRole("button", { name: "提交新版本", exact: true })).toBeDisabled();
  await p.getByLabel("编辑 Run ID", { exact: true }).fill("r4-A");
  await p.getByRole("button", { name: "选择 Run 已发布输出", exact: true }).click();
  await p.getByLabel("Run 已发布输出", { exact: true }).selectOption("ai-output");
  await p.getByRole("button", { name: "提交新版本", exact: true }).click();
  await expect(p.getByRole("alert")).toContainText("离线交接");
  await expect(p.getByLabel("编辑 Run ID", { exact: true })).toHaveValue("r4-A");
});
for (const [kind, tab, query, text] of [
  ["run:r4-A", "resources", "case=empty", "当前没有可展示的候选资料"],
  ["run:r4-A", "overview", "case=denied", "对象不可用"],
  ["artifact:r42-B", "preview", "artifactState=failed", "生成失败"],
  ["artifact:r42-B", "preview", "artifactState=running", "正在生成"],
  ["artifact:r42-B", "preview", "case=denied", "对象不可用"],
  ["artifact:r42-B", "preview", "case=empty", "暂无版本内容"],
  ["file:ai-output-node", "preview", "case=denied", "空间加载失败，请重试。"],
  ["task:ai-task", "overview", "case=denied", "对象不可用"],
  ["run:r4-A", "advanced", "visibility=none", "当前账号不可查看 Model Input"],
  ["run:r4-A", "advanced", "visibility=summary", "此账号仅可查看安全元数据摘要"],
])
  await check(`state-${kind.split(":")[0]}-${query.replace("=", "-")}`, async () => {
    const p = await scene(kind, tab, query);
    if (query === "visibility=summary")
      await p.getByRole("button", { name: "查看模型记录 model-fi" }).click();
    await expect(p.locator(".hp-inspector")).toContainText(text);
    if (query.includes("visibility="))
      await expect(p.getByTestId("model-input-provider-body")).toHaveCount(0);
  });
for (const width of [390, 780, 1279, 1280, 1680]) {
  for (const [kind, tab] of [
    ["run:r4-A", "advanced"],
    ["artifact:r42-B", "preview"],
    ["file:ai-output-node", "usage"],
    ["task:ai-task", "overview"],
  ]) {
    await check(`responsive-${width}-${kind.split(":")[0]}`, async () => {
      const p = await scene(kind, tab, "", width);
      if (width < 1280)
        await expect(p.locator(".hp-inspector")).toHaveAttribute("aria-modal", "true");
      expect(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await p.locator(".hp-inspector-expand").click();
      expect(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await shot(p, `responsive-${width}-${kind.split(":")[0]}-expanded`);
      await p.setViewportSize({ width, height: 480 });
      await expect(
        p.locator(".hp-inspector > .hp-surface-header button[title=关闭]"),
      ).toBeVisible();
    });
  }
}
await browser.close();
if (reports.some((r) => r.result === "fail") || errors.length || network.length)
  process.exitCode = 1;
