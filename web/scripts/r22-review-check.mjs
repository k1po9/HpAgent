/* global window, document, getComputedStyle, visualViewport */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
import { auditContrast } from "./r13-material-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-03");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const c = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
});
const p = await c.newPage();
p.setDefaultTimeout(10000);
const errors = [],
  checks = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.goto("http://127.0.0.1:5311/REVIEW.html");
const f = p.frameLocator("#lab");
await expect(f.locator(".hp-vitality--detail")).toBeVisible({ timeout: 20000 });
for (const state of ["thinking", "tool", "waiting", "succeeded", "failed", "running"]) {
  await p.locator('[data-state="' + state + '"]').click();
  await expect(f.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", state, {
    timeout: 20000,
  });
  await p.waitForTimeout(700);
  checks.push({ name: "interactive " + state, result: "pass" });
}
await p.locator("#quick").click();
await expect(f.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", "failed");
await p.waitForTimeout(700);
expect(await f.locator(".hp-vitality__layer").count()).toBe(
  await f.locator(".hp-vitality").count(),
);
checks.push({ name: "review quick latest wins", result: "pass" });
const media = await p.locator("video").evaluateAll(
  async (ns) =>
    await Promise.all(
      ns.map(
        (v) =>
          new Promise((r) => {
            if (v.readyState >= 1)
              return r({ src: v.getAttribute("src"), duration: v.duration, ready: true });
            v.addEventListener(
              "loadedmetadata",
              () => r({ src: v.getAttribute("src"), duration: v.duration, ready: true }),
              { once: true },
            );
            v.addEventListener("error", () => r({ src: v.getAttribute("src"), ready: false }), {
              once: true,
            });
          }),
      ),
    ),
);
expect(media.every((v) => v.ready)).toBe(true);
await p.screenshot({ path: resolve(out, "screenshots/review-page.png"), fullPage: true });
// Actual compiled application in the review-only fixture: pending approvals via existing resources query.
await p.goto("http://127.0.0.1:5311/lab.html");
await expect(p.locator(".hp-vitality--detail")).toBeVisible();
const runId = await p.evaluate(() => window.__reviewSeed.run.run_id);
await p.route("**/api/v1/runs/" + runId + "/file-action-approvals", (route) =>
  route.fulfill({
    json: {
      approvals: [
        {
          approval_id: "review-approval",
          run_id: runId,
          status: "pending",
          action: "read",
          file_id: "review-file",
          created_at: new Date().toISOString(),
          expires_at: null,
        },
      ],
    },
  }),
);
await p.getByRole("tab", { name: "使用资料与输出" }).click();
await p.waitForTimeout(300);
await p.getByRole("tab", { name: "概览" }).click();
await expect(p.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", "waiting");
await expect(p.locator(".hp-vitality-facts")).toContainText("有待审批");
await p.screenshot({ path: resolve(out, "screenshots/approvals-pending.png") });
checks.push({ name: "pending approval owns existing resources query", result: "pass" });
// Desktop six-state contrast, CSS phase offsets and signal box dimensions.
await p.reload();
await expect(p.locator(".hp-vitality--detail")).toBeVisible();
await p.emulateMedia({ reducedMotion: "no-preference" });
const contrast = [];
let delays = [];
for (const [phase, state] of [
  ["starting", "running"],
  ["calling_model", "thinking"],
  ["executing_tool", "tool"],
  ["waiting_for_account_execution", "waiting"],
]) {
  await p.evaluate(
    ({ phase }) => window.__reviewEmit("run.progress", { phase, summary: "阶段提示" }),
    { phase },
  );
  await expect(p.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", state);
  await p.waitForTimeout(700);
  contrast.push({ state, audit: await auditContrast(p) });
  if (state === "tool")
    delays = await p
      .locator(".hp-vitality--detail .hv-node")
      .evaluateAll((ns) => ns.map((n) => getComputedStyle(n).animationDelay));
}
expect(new Set(delays).size).toBeGreaterThan(1);
const sizes = await p.locator(".hp-vitality__frame").evaluateAll((ns) =>
  ns.map((n) => ({
    width: n.getBoundingClientRect().width,
    height: n.getBoundingClientRect().height,
  })),
);
expect(sizes.some((x) => x.width === 38)).toBe(true);
expect(sizes.some((x) => x.width === 84)).toBe(true);
// Actual browser zoom 200% on fixture; viewport and key content remain reachable.
const session = await c.newCDPSession(p);
await session.send("Emulation.setPageScaleFactor", { pageScaleFactor: 2 });
await p.waitForTimeout(300);
await p.screenshot({ path: resolve(out, "screenshots/c-200-page-scale.png") });
const zoom = await p.evaluate(() => ({
  scale: visualViewport.scale,
  width: visualViewport.width,
  doc: document.documentElement.scrollWidth,
}));
checks.push({
  name: "CDP page scale 200%",
  result: "pass",
  zoom,
  limit:
    "page scale is not Chrome page-zoom font reflow; separate r22-accessibility checks browser zoom",
});
await session.send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 });
await p.setViewportSize({ width: 390, height: 568 });
await expect(p.locator(".hp-vitality--detail")).toBeVisible();
await p.screenshot({ path: resolve(out, "screenshots/c-390-short.png") });
checks.push({ name: "short mobile detail", result: "pass" });
// Reference HTML exercised separately; never counted as product evidence.
await mkdir(resolve(out, "references"), { recursive: true });
for (const [name, path, button] of [
  ["C", "hpagent-agent-vitality/assets/C3-Chroma-Continuity-Lab.html", "Thinking"],
  ["B", "hpagent-living-components-spatial/assets/B6-Spatial-Grammar-Lab.html", "打开 Surface"],
  ["A", "hpagent-input-action/assets/A2.1-Ember-Relay-Lab.html", null],
]) {
  const url =
    "file:////wsl.localhost/Ubuntu-22.04/home/hp/workspace/HpAgent/ui_skill_delete_after_done/" +
    path;
  await p.goto(url);
  await p.waitForTimeout(300);
  if (button) {
    const target = p.getByRole("button", { name: button, exact: false }).first();
    if (await target.count()) await target.click();
  }
  await p.screenshot({ path: resolve(out, "references/" + name + "-opened.png") });
  checks.push({ name: "reference " + name + " HTML opened", result: "reference-only" });
}
await writeFile(
  resolve(out, "logs/review-page-check.json"),
  JSON.stringify(
    { browser: await b.version(), checks, media, delays, sizes, contrast, errors },
    null,
    2,
  ),
);
console.log(checks);
await b.close();
