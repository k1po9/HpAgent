import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-01");
const routes = JSON.parse(
  await readFile(resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/routes.json")),
);
await mkdir(resolve(out, "baseline"), { recursive: true });
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const ctx = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  recordVideo: { dir: resolve(out, "baseline"), size: { width: 1680, height: 1000 } },
  reducedMotion: "reduce",
});
const p = await ctx.newPage();
await p.goto("http://127.0.0.1:5310/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.emulateMedia({ reducedMotion: "no-preference" });
for (const width of [1680, 1280, 780, 390]) {
  await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
  await p.goto("http://127.0.0.1:5310/" + routes.user);
  await expect(p.locator(".hp-composer")).toBeVisible();
  await p.waitForTimeout(800);
  await p.screenshot({ path: resolve(out, `baseline/ai-${width}.png`) });
  await p.getByRole("button", { name: "账户设置", exact: true }).click();
  await p.waitForTimeout(450);
  await p.screenshot({ path: resolve(out, `baseline/account-${width}.png`) });
  await p.keyboard.press("Escape");
}
await p.setViewportSize({ width: 1680, height: 1000 });
for (const name of ["空间", "任务", "AI"]) {
  await p.getByRole("button", { name, exact: true }).first().click();
  await p.waitForTimeout(800);
}
await p.goto("http://127.0.0.1:5310/" + routes.user);
await p.waitForTimeout(500);
const ids = await p
  .locator("[data-conversation-id]")
  .evaluateAll((ns) => ns.slice(0, 3).map((n) => n.dataset.conversationId));
for (const id of ids) {
  await p.locator(`[data-conversation-id="${id}"]`).click();
  await p.waitForTimeout(900);
}
await writeFile(
  resolve(out, "logs/baseline-browser.json"),
  JSON.stringify(
    { browser: await b.version(), routes, ids, data: "real isolated API; no provider calls" },
    null,
    2,
  ),
);
await ctx.close();
await b.close();
