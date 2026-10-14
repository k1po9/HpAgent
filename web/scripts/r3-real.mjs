import { URL } from "node:url";
/* global window,document,getComputedStyle,requestAnimationFrame,performance */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
for (const d of ["screenshots", "clips"]) await mkdir(resolve(out, d), { recursive: true });
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const c = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  recordVideo: { dir: resolve(out, "clips"), size: { width: 1680, height: 1000 } },
});
const p = await c.newPage();
p.setDefaultTimeout(10000);
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.goto("http://127.0.0.1:5294/#/ai");
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
await p.emulateMedia({ reducedMotion: "no-preference" });
await p.evaluate(() => {
  window.__samples = [];
  const started = performance.now();
  const tick = () => {
    const n = document.querySelector(".hp-action-relay");
    window.__samples.push({
      t: performance.now() - started,
      phase: n?.dataset.phase,
      head: n?.dataset.head,
      travel: n?.dataset.travelLength,
      arc: n?.dataset.arcFraction,
      run: n?.dataset.runId,
      rotor: [...document.querySelectorAll(".hp-vitality--compact .hv-rotor")].map((n) => ({
        animation: getComputedStyle(n).animationName,
        opacity: getComputedStyle(n).opacity,
      })),
      count: document.querySelectorAll(".hp-action-relay").length,
    });
    if (performance.now() - started < 5500) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
await p.getByLabel("消息输入").fill("R3隔离验收：确认后只接力一次，不调用外部供应商。");
const accepted = p.waitForResponse(
  (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/messages"),
);
await p.getByRole("button", { name: "发送", exact: true }).click();
const dto = await (await accepted).json();
await p.waitForTimeout(5000);
await p.screenshot({ path: resolve(out, "screenshots/real-terminal.png") });
const samples = await p.evaluate(() => window.__samples);
await writeFile(
  resolve(out, "logs/real.json"),
  JSON.stringify(
    { acceptedStatus: dto.run.status, runId: dto.run.run_id, samples, errors },
    null,
    2,
  ),
);
await expect(p.locator(".hp-action-relay")).toHaveCount(0);
expect(errors).toEqual([]);
const video = p.video();
await c.close();
await video.saveAs(resolve(out, "clips/real.webm"));
await b.close();
