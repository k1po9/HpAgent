/* global window, document, getComputedStyle, console, process */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { mkdir, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-15-task-relay");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const reports = [],
  errors = [];
const cases = [
  ["editor", "overview", "修改要求", "修改任务要求"],
  ["budget", "overview", "提高预算", "提高任务预算"],
  ["stop", "overview", "停止任务", "停止任务"],
  ["link", "overview", "关联对话", "关联任务到对话"],
  ["acceptance", "outputs", "接受这份成果", "接受指定成果版本"],
  ["delivery", "outputs", "确认已送达", "确认投递决策"],
  ["target", "advanced", "停用此通知目标", "停用通知目标"],
];
await mkdir(out + "/screenshots/dialogs", { recursive: true });
try {
  for (const [name, tab, button, title] of cases) {
    const pair = [];
    for (const stage of ["before", "after"]) {
      const page = await browser.newPage({
        viewport: { width: 1680, height: 1000 },
        reducedMotion: "reduce",
        locale: "zh-CN",
        timezoneId: "Asia/Shanghai",
      });
      page.on("pageerror", (e) => errors.push({ name, stage, error: e.message }));
      try {
        await page.goto(
          pathToFileURL(out + `/offline/${stage.toUpperCase()}.React.Standalone.html`).href +
            `#/ai/01a13187-fc64-7b62-bc70-bc5a6d25c0ad?inspect=task%3Aai-task&tab=${tab}`,
        );
        await page.waitForFunction(() => window.__handoffBooted);
        await page.locator('.hp-entry[data-entering="false"]').waitFor();
        await page
          .locator(".hp-inspector")
          .getByRole("button", { name: button, exact: true })
          .click();
        const dialog = page.getByRole("dialog", { name: title, exact: true });
        await expect(dialog).toBeVisible();
        await expect(page.locator("dialog[open]:not(.hp-surface--region)")).toHaveCount(1);
        if (name === "editor") await expect(page.getByLabel("目标 / 提醒内容")).toBeVisible();
        await page.waitForTimeout(100);
        const state = await dialog.evaluate((n) => {
          const r = n.getBoundingClientRect(),
            s = getComputedStyle(n);
          return {
            rect: [r.x, r.y, r.width, r.height],
            styles: { background: s.backgroundColor, border: s.borderRadius, font: s.fontFamily },
            text: n.textContent,
            fields: [...n.querySelectorAll("input,textarea,select")].map((f) => ({
              tag: f.tagName,
              value: f.value,
              disabled: f.disabled,
            })),
            focus: document.activeElement?.getAttribute("aria-label"),
          };
        });
        await dialog.screenshot({ path: `${out}/screenshots/dialogs/${name}-${stage}.png` });
        pair.push({ stage, ...state });
      } finally {
        await page.close();
      }
    }
    const before = pair[0],
      after = pair[1];
    const same =
      JSON.stringify({ ...before, stage: null }) === JSON.stringify({ ...after, stage: null });
    reports.push({ name, result: same ? "pass" : "difference", pair });
    console.log(name, same ? "pass" : "difference");
  }
} finally {
  await browser.close();
  await writeFile(
    out + "/evidence/dialog-matrix.json",
    JSON.stringify({ reports, errors }, null, 2),
  );
}
if (errors.length || reports.some((r) => r.result !== "pass")) process.exitCode = 1;
