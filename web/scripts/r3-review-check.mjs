import process from "node:process";
import "./review-runtime.mjs";
/* global getComputedStyle */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const c = await b.newContext({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" });
const p = await c.newPage();
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.emulateMedia({ reducedMotion: "no-preference" });
await p.goto("http://127.0.0.1:5317/focus.html");
const f = p.frameLocator("#lab");
await expect(f.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await f.getByLabel("消息输入").fill("R3专注评审页实际操作");
await f.getByRole("button", { name: "发送", exact: true }).click();
await expect(f.locator(".hp-vitality--compact").last()).toHaveAttribute("data-vitality", "waiting");
await p.waitForTimeout(600);
await p.getByRole("button", { name: "开始 Running", exact: true }).click();
await expect(f.locator(".hp-action-relay")).toHaveAttribute("data-phase", "travelling");
await expect(f.locator(".hp-action-relay")).toHaveCount(0);
await p.getByRole("button", { name: "成功", exact: true }).click();
await f.getByLabel("消息输入").fill("原请求确认后保留后续草稿");
await p.mouse.move(0, 0);
await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
await p.screenshot({ path: resolve(out, "screenshots/focus-review.png") });
await p.goto("http://127.0.0.1:5317/REVIEW.html");
const before = p.frameLocator("#before"),
  after = p.frameLocator("#after");
for (const frame of [before, after]) {
  await expect(frame.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  await frame.getByLabel("消息输入").fill("同数据对照");
  await frame.getByRole("button", { name: "发送", exact: true }).click();
  await expect(frame.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "waiting",
  );
}
await p.waitForTimeout(600);
await p.getByRole("button", { name: "开始 Running（两侧）", exact: true }).click();
await expect(before.locator(".hp-action-relay")).toHaveCount(0);
await expect(after.locator(".hp-action-relay")).toHaveAttribute("data-phase", "travelling");
expect(
  await before
    .locator(".hp-vitality--compact")
    .last()
    .locator(".hv-rotor")
    .evaluate((n) => getComputedStyle(n).animationName),
).not.toBe("none");
await expect(after.locator(".hp-action-relay")).toHaveCount(0);
const media = await p.locator("video").evaluate(
  (v) =>
    new Promise((resolve) => {
      if (v.readyState >= 1) return resolve({ ready: true, duration: v.duration });
      v.addEventListener("loadedmetadata", () => resolve({ ready: true, duration: v.duration }), {
        once: true,
      });
      v.addEventListener("error", () => resolve({ ready: false }), { once: true });
    }),
);
expect(media.ready).toBe(true);
expect(errors).toEqual([]);
await writeFile(
  resolve(out, "logs/review-page.json"),
  JSON.stringify({ focus: "pass", sameDataBeforeAfter: "pass", media, errors }, null, 2),
);
await b.close();
