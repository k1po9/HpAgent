import process from "node:process";
import "./review-runtime.mjs";
import "./r1-isolation.mjs";
/* global getComputedStyle */
import console from "node:console";
import { chromium, expect } from "@playwright/test";
import { writeFile, readFile } from "node:fs/promises";
const out = "artifacts/product-acceptance/ui-iteration/R1/attempt-01";
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
await p.goto("http://127.0.0.1:5294/?r1=s1");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
await p.goto(
  "http://127.0.0.1:5294/?r1=s1" + JSON.parse(await readFile(out + "/routes.json")).ST03,
);
await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
await expect(p.locator(".hp-text-part h2")).toBeVisible();
const cdp = await p.context().newCDPSession(p);
await cdp.send("DOM.enable");
await cdp.send("CSS.enable");
const { root } = await cdp.send("DOM.getDocument");
const { nodeId } = await cdp.send("DOM.querySelector", {
  nodeId: root.nodeId,
  selector: '.hp-msg[data-role="assistant"] .hp-text-part p',
});
const fonts = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
const styles = await p
  .locator('.hp-msg[data-role="assistant"] .hp-text-part p')
  .first()
  .evaluate((e) => {
    let arr = [];
    for (let n = e; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      arr.push({
        class: n.className,
        font: s.fontFamily,
        size: s.fontSize,
        line: s.lineHeight,
        display: s.display,
        gap: s.gap,
        margin: s.margin,
        padding: s.padding,
      });
    }
    return arr;
  });
await writeFile(out + "/logs/fonts.json", JSON.stringify({ fonts, styles }, null, 2));
console.log(fonts);
await b.close();
