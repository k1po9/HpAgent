import process from "node:process";
import "./review-runtime.mjs";
/* global document, getComputedStyle */
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" });
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.goto("http://127.0.0.1:5294/#/ai/01a13187-fc64-7b62-bc70-bc5a6d25c0ad");
  await expect(p.locator(".hp-msg[data-role=assistant]")).toBeVisible();
  const c = await p.context().newCDPSession(p);
  await c.send("DOM.enable");
  await c.send("CSS.enable");
  const root = await c.send("DOM.getDocument");
  const fonts = {};
  for (const s of [".hp-shell", ".hp-text-part", ".hp-conv-title", ".hp-canvas-header h1"]) {
    const n = await c.send("DOM.querySelector", { nodeId: root.root.nodeId, selector: s });
    fonts[s] = await c.send("CSS.getPlatformFontsForNode", { nodeId: n.nodeId });
  }
  const styles = await p.evaluate(() =>
    Object.fromEntries(
      [".hp-shell", ".radix-themes", ".hp-text-part", ".hp-conv-title"].map((s) => {
        const e = document.querySelector(s),
          c = getComputedStyle(e);
        return [s, { font: c.fontFamily, color: c.color, weight: c.fontWeight }];
      }),
    ),
  );
  console.log(JSON.stringify({ fonts, styles }));
  await writeFile(
    resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-03/logs/font-before.json"),
    JSON.stringify({ fonts, styles }, null, 2),
  );
  await p.screenshot({
    path: resolve(
      "artifacts/product-acceptance/ui-iteration/R1.3/attempt-03/screenshots-before.png",
    ),
  });
} finally {
  await b.close();
}
