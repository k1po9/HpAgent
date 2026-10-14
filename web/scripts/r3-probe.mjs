import process from "node:process";
/* global window,document,getComputedStyle,requestAnimationFrame,performance */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
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
p.on("console", (m) => {
  if (m.text().startsWith("R3")) process.stdout.write(m.text() + "\n");
});
const errors = [],
  checks = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
async function scene(name, motion = "no-preference", width = 1680) {
  await p.emulateMedia({ reducedMotion: motion });
  await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
  await p.goto("http://127.0.0.1:5317/lab.html?case=" + (name === "running" ? "queued" : name));
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await p.getByLabel("消息输入").fill("R3明确夹具：原请求接力");
  await p.getByRole("button", { name: "发送", exact: true }).click();
  if (name === "running") {
    await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
      "data-vitality",
      "waiting",
    );
    await p.waitForTimeout(400);
    await p.evaluate(() => window.__reviewStatus("running"));
  }
}
const relay = p.locator(".hp-action-relay");
await scene("queued");
await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute("data-vitality", "waiting");
await expect(relay).toHaveCount(0);
checks.push({ case: "confirmed queued stays Waiting", pass: true });
await p.waitForTimeout(400);
await p.evaluate(() => {
  window.__frames = [];
  const began = performance.now();
  const tick = () => {
    const n = document.querySelector(".hp-action-relay"),
      ring = document.querySelector(
        '.hp-vitality--compact[data-handoff-run="r3-review-1"] .hv-rotor',
      );
    window.__frames.push({
      t: performance.now() - began,
      phase: n?.dataset.phase,
      head: Number(n?.dataset.head),
      travel: Number(n?.dataset.travelLength),
      arc: Number(n?.dataset.arcFraction),
      count: document.querySelectorAll(".hp-action-relay").length,
      rotation: ring && getComputedStyle(ring).animationName,
      opacity: ring && getComputedStyle(ring).opacity,
    });
    if (performance.now() - began < 2200) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  window.__reviewStatus("running");
});
await p.waitForTimeout(2000);
const report = await p.evaluate(() => ({
  frames: window.__frames,
  dialogs: [...document.querySelectorAll("dialog[open]")].map((n) => ({
    id: n.id,
    cls: n.className,
    display: getComputedStyle(n).display,
  })),
  nodes: [...document.querySelectorAll(".hp-vitality--compact")].map((n) => ({
    data: { ...n.dataset },
    rect: n.getBoundingClientRect().toJSON(),
  })),
  source: document.querySelector(".hp-composer__action").getBoundingClientRect().toJSON(),
}));
await writeFile(resolve(out, "logs/probe.json"), JSON.stringify(report, null, 2));
await b.close();
