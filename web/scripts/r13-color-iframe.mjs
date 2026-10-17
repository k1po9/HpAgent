import process from "node:process";
import "./review-runtime.mjs";
/* global window */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-03");
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "no-preference",
});
const results = [];
const errors = [];
p.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  for (const base of ["http://127.0.0.1:5300", "http://127.0.0.1:5294"]) {
    await p.goto(base + "/" + routes.ST02);
    await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
    await p.locator(".hp-artifact-message-card").first().click();
    const iframe = p.getByTitle("Artifact 预览");
    await expect(iframe).toBeVisible();
    const body = p.frameLocator("iframe").locator("body");
    await expect(body).not.toBeEmpty();
    const button = p.frameLocator("iframe").getByRole("button", { name: "切换状态" });
    await button.evaluate((el) =>
      el.addEventListener("click", (e) => {
        window.colorProbeClicks = (window.colorProbeClicks || []).concat({ trusted: e.isTrusted });
      }),
    );
    const before = await body.innerText();
    const scripts = await p.frameLocator("iframe").locator("script").allTextContents();
    const bounds = await button.boundingBox();
    const frame = await iframe.boundingBox();
    await button.click();
    const after = await body.innerText();
    const mouse = await body.evaluate(() => window.colorProbeClicks || []);
    await button.focus();
    await p.keyboard.press("Enter");
    const keyboard = await body.innerText();
    results.push({ base, before, after, keyboard, mouse, scripts, bounds, frame });
  }
  console.log(JSON.stringify({ results, errors }));
} finally {
  await writeFile(
    resolve(out, "logs/iframe-investigation.json"),
    JSON.stringify({ results, errors }, null, 2),
  );
  await b.close();
}
