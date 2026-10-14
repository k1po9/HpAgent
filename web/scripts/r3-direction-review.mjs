/* global window, document, getComputedStyle, requestAnimationFrame, performance */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { URL } from "node:url";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-02");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const c = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  recordVideo: { dir: resolve(out, "clips"), size: { width: 1680, height: 1000 } },
});
const p = await c.newPage();
const epoch = Date.now(),
  errors = [],
  network = [];
p.on("pageerror", (e) => errors.push(e.message));
p.on("response", (r) => {
  if (new URL(r.url()).pathname.startsWith("/auth/") || new URL(r.url()).pathname === "/api/v1/me")
    network.push({ path: new URL(r.url()).pathname, status: r.status() });
});
// Intentionally never visit/log into 5294. Fresh same-origin review login is the regression.
await p.goto("http://127.0.0.1:5317/focus.html");
const f = p.frameLocator("#lab");
await f.getByLabel("用户名").fill("r1_review");
await f.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await f.getByRole("button", { name: "登录", exact: true }).click();
await expect(f.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await expect(f.getByLabel("消息输入")).toBeVisible();
expect(network.some((x) => x.path === "/auth/login" && x.status === 303)).toBe(true);
expect(network.some((x) => x.path === "/api/v1/me" && x.status === 200)).toBe(true);
await p.emulateMedia({ reducedMotion: "no-preference" });
await f.getByLabel("消息输入").fill("R3返修：补环与稳定旋转统一逆时针。");
await f.getByRole("button", { name: "发送", exact: true }).click();
await expect(f.locator(".hp-vitality--compact").last()).toHaveAttribute("data-vitality", "waiting");
await p.waitForTimeout(600);
const answer = await f
    .locator('[data-message-id="r3-assistant-1"] .hp-answer-surface')
    .boundingBox(),
  composer = await f.locator(".hp-composer").boundingBox(),
  ring = await f.locator(".hp-vitality--compact").last().boundingBox();
const box = {
  x: answer.x,
  y: answer.y - 32,
  width: answer.width,
  height: composer.y + composer.height - answer.y + 32,
};
// Frame position getters include parent placement. Recording is of the full parent viewport.
await f.locator(".hp-composer").evaluate(() => {
  window.__direction = [];
  const began = performance.now();
  function sample() {
    const svg = document.querySelector(".hp-action-relay"),
      rotor = document.querySelector(
        '.hp-vitality--compact[data-handoff-run="r3-review-1"] .hv-rotor',
      );
    let record = {
      t: performance.now() - began,
      phase: svg?.dataset.phase,
      arc: Number(svg?.dataset.arcFraction),
    };
    if (svg && record.phase === "forming") {
      const dot = svg.querySelector("circle");
      record.x = Number(dot.getAttribute("cx"));
      record.y = Number(dot.getAttribute("cy"));
    }
    if (rotor) {
      const s = getComputedStyle(rotor);
      record.animation = s.animationName;
      record.transform = s.transform;
      record.opacity = s.opacity;
    }
    window.__direction.push(record);
    if (performance.now() - began < 5600) requestAnimationFrame(sample);
  }
  requestAnimationFrame(sample);
});
const start = (Date.now() - epoch) / 1000;
await p.getByRole("button", { name: "开始 Running", exact: true }).click();
await expect(f.locator(".hp-action-relay")).toHaveAttribute("data-phase", "travelling");
await expect(f.locator(".hp-action-relay")).toHaveCount(0);
await p.waitForTimeout(4000);
const samples = await f.locator(".hp-composer").evaluate(() => window.__direction);
const captured = samples.filter(
  (s) => !s.phase && s.animation === "hv-spin" && s.transform !== "none",
);
const angles = captured.map((s) => {
  const a = s.transform
    .match(/matrix\(([^)]+)\)/)[1]
    .split(",")
    .map(Number);
  return (Math.atan2(a[1], a[0]) * 180) / Math.PI;
});
const deltas = angles.slice(1).map((a, i) => ((a - angles[i] + 540) % 360) - 180);
expect(deltas.filter((x) => x < -0.01).length).toBeGreaterThan(20);
expect(deltas.every((x) => x <= 0.01)).toBe(true);
expect(samples.filter((s) => s.phase).every((s) => s.animation === "none")).toBe(true);
const forming = samples.filter((s) => s.phase === "forming" && Number.isFinite(s.x));
expect(forming.length).toBeGreaterThan(2);
const center = { x: ring.x + ring.width / 2, y: ring.y - 112 + ring.height / 2 };
const a = forming.map((s) => (Math.atan2(s.y - center.y, s.x - center.x) * 180) / Math.PI);
const d = a.slice(1).map((n, i) => ((n - a[i] + 540) % 360) - 180);
expect(d.every((n) => n <= 0.1)).toBe(true);
await p.emulateMedia({ reducedMotion: "reduce" });
expect(
  await f
    .locator(".hv-rotor")
    .last()
    .evaluate((n) => getComputedStyle(n).animationName),
).toBe("none");
expect(errors).toEqual([]);
await writeFile(
  resolve(out, "logs/review.json"),
  JSON.stringify(
    {
      freshReviewLogin: "pass",
      network,
      formingCounterclockwise: true,
      capturedCounterclockwise: true,
      capturedSamples: captured.length,
      formingSamples: forming.length,
      deltas,
      samples,
      reduce: "pass",
      errors,
    },
    null,
    2,
  ),
);
const video = p.video();
await c.close();
await video.saveAs(resolve(out, "clips/capture-raw.webm"));
await b.close();
await writeFile(
  resolve(out, "logs/capture-timing.json"),
  JSON.stringify({ start, box, ring, duration: 5.7 }, null, 2),
);
