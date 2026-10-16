/* global window, document, innerWidth, URLSearchParams, console */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { mkdir, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-12-ai-inspector-handoff");
await mkdir(out + "/frames", { recursive: true });
const file = pathToFileURL(out + "/HpAgent-AI-Inspector-Standalone-Editable.html").href;
const conversation = "01a13187-fc64-7b62-bc70-bc5a6d25c0ad";
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const reports = [],
  errors = [],
  network = [];
let failure;
async function pageFor(kind, tab, query = "", width = 1680) {
  const p = await browser.newPage({
    viewport: { width, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  });
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("request", (r) => {
    if (/^https?:/.test(r.url())) network.push(r.url());
  });
  const params = new URLSearchParams({ inspect: kind, tab });
  if (kind.startsWith("artifact:")) params.set("version", "r42-B-v2");
  await p.goto(file + (query ? "?" + query : "") + "#/ai/" + conversation + "?" + params);
  await p.waitForFunction(() => window.__handoffBooted === true);
  await expect(p.locator(".hp-inspector")).toBeVisible();
  return p;
}
try {
  const matrix = [
    ["run:r4-A", "overview", "概览", ".hp-vitality-facts"],
    ["run:r4-A", "resources", "使用资料与输出", '[aria-label="使用资料与输出"]'],
    ["run:r4-A", "advanced", "高级诊断", ".hp-trace-tree__node"],
    ["artifact:r42-B", "preview", "预览", 'iframe[title="Artifact 预览"]'],
    ["artifact:r42-B", "versions", "版本历史", ".hp-artifact-history"],
    ["artifact:r42-B", "details", "详情", ".hp-artifact-details"],
    ["file:ai-output-node", "preview", "预览", ".hp-file-preview"],
    ["file:ai-output-node", "details", "详情", '[aria-label="保留与空间"]'],
    ["file:ai-output-node", "versions", "版本历史", '[aria-label="Workspace 版本"]'],
    ["file:ai-output-node", "usage", "AI 使用范围", ".hp-subject-picker"],
    ["file:ai-folder", "details", "详情", ".hp-object-path"],
    ["file:ai-folder", "usage", "AI 使用范围", ".hp-subject-picker"],
    ["task:ai-task", "overview", "概览", '[aria-label="任务概览"]'],
    ["task:ai-task", "outputs", "成果与执行", ".hp-task-outputs"],
    ["task:ai-task", "resources", "使用资料", ".hp-operation-form"],
    ["task:ai-task", "advanced", "高级详情", 'input[name="run"]'],
  ];
  for (const [kind, tab, label, selector] of matrix) {
    const p = await pageFor(kind, tab);
    await expect(p.getByRole("tab", { name: label, exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(p.locator(selector).first()).toBeVisible();
    if (kind === "file:ai-folder") await expect(p.getByRole("tab")).toHaveCount(2);
    await p.locator(".hp-inspector-expand").click();
    await expect(p.locator(".hp-inspector")).toHaveClass(/hp-inspector--expanded/);
    expect(
      await p.locator(".hp-inspector").evaluate((n) => Math.round(n.getBoundingClientRect().width)),
    ).toBe(460);
    await p.screenshot({
      path: out + "/frames/" + kind.replace(":", "-") + "-" + tab + "-expanded.png",
    });
    await p.locator(".hp-inspector-expand").click();
    expect(
      await p.locator(".hp-inspector").evaluate((n) => Math.round(n.getBoundingClientRect().width)),
    ).toBe(390);
    reports.push({ kind, tab, normalAndExpanded: "pass" });
    await p.close();
  }
  const run = await pageFor("run:r4-A", "advanced");
  await run.getByText("预算与模型用量", { exact: true }).click();
  await expect(run.getByTestId("run-token-usage")).toContainText("6.4");
  await run.locator(".hp-trace-tree__node").filter({ hasText: "阅读资料并生成报告" }).click();
  await expect(run.locator('[aria-label="Trace 节点详情"]')).toContainText("LLM Call");
  await run.getByRole("button", { name: "查看 Model Input", exact: true }).click();
  await expect(
    run.locator('[aria-label="Trace 节点详情"]').getByTestId("model-input-provider-body"),
  ).toContainText("明确离线夹具");
  await run.getByRole("button", { name: "查看模型记录 model-fi" }).click();
  await expect(run.getByTestId("model-input-full_safe")).toHaveCount(2);
  await run.screenshot({ path: out + "/frames/run-model-input-open.png" });
  reports.push({ case: "Trace-and-ModelInput", result: "pass" });
  await run.close();
  const res = await pageFor("run:r4-A", "resources");
  await expect(
    res.getByRole("button", { name: "项目资料/项目评审说明.md", exact: true }),
  ).toBeVisible();
  await res.getByRole("button", { name: "保存到空间", exact: true }).click();
  await expect(res.getByRole("dialog", { name: "保存到长期文件", exact: true })).toBeVisible();
  await res.screenshot({ path: out + "/frames/save-dialog.png" });
  await res.keyboard.press("Escape");
  await expect(res.getByRole("dialog", { name: "保存到长期文件", exact: true })).toHaveCount(0);
  await res.getByRole("button", { name: "项目资料/项目评审说明.md", exact: true }).click();
  await expect(res.locator(".hp-file-preview")).toBeVisible();
  await res.getByRole("button", { name: "返回", exact: true }).click();
  await expect(res.getByRole("tab", { name: "使用资料与输出", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  reports.push({ case: "Run-File-Back-and-save", result: "pass" });
  await res.close();
  const art = await pageFor("artifact:r42-B", "preview");
  const preview = art.frameLocator('iframe[title="Artifact 预览"]');
  await preview.locator("#counter").click();
  await art.getByLabel("修改指令").fill("保留这份美化评审草稿");
  await art.getByRole("tab", { name: "详情", exact: true }).click();
  await art.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(preview.locator("#counter")).toHaveText("1");
  await expect(art.getByLabel("修改指令")).toHaveValue("保留这份美化评审草稿");
  await art.getByRole("button", { name: "生成新版本", exact: true }).click();
  await expect(art.getByRole("alert")).toContainText("离线交接");
  reports.push({ case: "Artifact-state-and-write-denial", result: "pass" });
  await art.close();
  const usage = await pageFor("file:ai-output-node", "usage");
  await usage.getByLabel(/^目标对话/).selectOption(conversation);
  await expect(usage.locator('[aria-label="主体权限编辑"]')).toBeVisible();
  await usage.screenshot({ path: out + "/frames/file-permissions.png" });
  reports.push({ case: "file-permissions", result: "pass" });
  await usage.close();
  const task = await pageFor("task:ai-task", "overview");
  await task.getByRole("button", { name: "修改要求", exact: true }).click();
  await expect(task.getByRole("dialog", { name: /修改/ })).toBeVisible();
  await task.screenshot({ path: out + "/frames/task-editor.png" });
  await task.keyboard.press("Escape");
  await task.getByRole("button", { name: "提高预算", exact: true }).click();
  await expect(task.getByRole("dialog", { name: /预算/ })).toBeVisible();
  reports.push({ case: "task-dialogs", result: "pass" });
  await task.close();
  for (const [kind, tab, query, text] of [
    ["run:r4-A", "resources", "case=empty", "当前没有可展示的候选资料"],
    ["run:r4-A", "overview", "case=denied", "对象不可用"],
    ["artifact:r42-B", "preview", "artifactState=failed", "生成失败"],
    ["run:r4-A", "advanced", "visibility=summary", "此账号仅可查看安全元数据摘要"],
    ["run:r4-A", "advanced", "visibility=none", "当前账号不可查看 Model Input"],
  ]) {
    const p = await pageFor(kind, tab, query);
    if (query === "visibility=summary")
      await p.getByRole("button", { name: "查看模型记录 model-fi" }).click();
    await expect(p.locator(".hp-inspector")).toContainText(text);
    reports.push({ case: query, result: "pass" });
    await p.close();
  }
  const mobile = await pageFor("artifact:r42-B", "preview", "", 390);
  await expect(mobile.locator(".hp-inspector")).toHaveAttribute("aria-modal", "true");
  expect(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
    true,
  );
  await mobile.screenshot({ path: out + "/frames/mobile-artifact.png" });
  reports.push({ case: "mobile", result: "pass" });
  await mobile.close();
  const tools = await pageFor("run:r4-A", "overview");
  await tools.locator("#handoff-open").click();
  await expect(tools.locator("#handoff-tools")).toBeVisible();
  await tools.locator("#handoff-page").selectOption("9");
  await tools.locator("#handoff-launch").click();
  await expect(tools.getByRole("tab", { name: "AI 使用范围", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  reports.push({ case: "scene-tool", result: "pass" });
  await tools.close();
  expect(errors).toEqual([]);
  expect(network).toEqual([]);
  console.log("AI Inspector handoff browser checks passed");
} catch (error) {
  failure = error.message;
  throw error;
} finally {
  await writeFile(
    out + "/browser-check.json",
    JSON.stringify({ reports, errors, externalRequests: network, failure }, null, 2),
  );
  await browser.close();
}
