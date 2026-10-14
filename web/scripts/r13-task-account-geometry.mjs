/* global document, getComputedStyle, window */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const p = await b.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.getByRole("button", { name: "账户设置", exact: true }).click();
  const m = await p.locator(".hp-account-settings").evaluate((el) => {
    const css = getComputedStyle(el);
    const props = {};
    for (const key of [
      "height",
      "minHeight",
      "maxHeight",
      "position",
      "top",
      "bottom",
      "margin",
      "display",
      "boxSizing",
      "alignSelf",
      "flex",
    ])
      props[key] = css[key];
    const matched = [];
    const walk = (rules) => {
      for (const r of rules) {
        if (r.conditionText && !window.matchMedia(r.conditionText).matches) continue;
        if (r.selectorText) {
          try {
            if (el.matches(r.selectorText) && /height|inset/.test(r.style.cssText))
              matched.push(r.cssText);
          } catch {
            /* Ignore inaccessible or unsupported stylesheet selectors. */
          }
        }
        if (r.cssRules) walk(r.cssRules);
      }
    };
    for (const sheet of document.styleSheets)
      try {
        walk(sheet.cssRules);
      } catch {
        /* Ignore inaccessible or unsupported stylesheet selectors. */
      }
    return { className: el.className, props, matched };
  });
  await writeFile(
    resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/logs/account-geometry.json"),
    JSON.stringify(m, null, 2),
  );
  console.log(JSON.stringify(m.props));
} finally {
  await b.close();
}
