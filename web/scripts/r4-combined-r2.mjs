import console from "node:console";
/* global window, document, getComputedStyle */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-02");
const b = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  }),
  c = await b.newContext({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "no-preference",
  }),
  p = await c.newPage(),
  checks = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
async function scene() {
  await p.goto("http://127.0.0.1:5319/lab.html");
  if (await p.getByLabel("用户名").isVisible()) {
    await p.getByLabel("用户名").fill("r1_review");
    await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
    await p.getByRole("button", { name: "登录", exact: true }).click();
  }
  await expect(p.locator(".hp-run-detail")).toHaveCount(3);
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.waitForTimeout(300);
}
const send = async () => {
  await p.getByLabel("消息输入").fill("R4连续操作夹具");
  await p.getByRole("button", { name: "发送", exact: true }).click();
};
try {
  await scene();
  await send();
  await p.waitForFunction(
    () => document.querySelector(".hp-action-relay")?.dataset.phase === "forming",
    null,
    { polling: 10 },
  );
  await p.locator("#run-open-r4-send").click();
  await expect(p.locator(".hp-action-relay")).toHaveCount(0);
  await expect(p.locator(".hp-inspector .hp-vitality")).toHaveAttribute("data-vitality", "waiting");
  checks.push("R3 forming → Inspector cancels A and keeps waiting");
  await scene();
  await send();
  await p.evaluate(() => window.__r4setState("running"));
  await p.locator("#run-open-r4-send").click();
  await p.evaluate(() => window.__r4progress("calling_model"));
  await expect(p.locator(".hp-inspector .hp-vitality")).toHaveAttribute(
    "data-vitality",
    "thinking",
  );
  await p.evaluate(() => window.__r4setState("failed"));
  await expect(p.locator(".hp-inspector .hp-vitality")).toHaveAttribute("data-vitality", "failed");
  checks.push("running→quick thinking→failed stays factual during B receiving/arrival");
  await scene();
  await send();
  await p.locator("#run-open-r4-send").click();
  await p.locator(".hp-composer__action").click();
  await expect(p.locator(".hp-inspector .hp-vitality")).toHaveAttribute(
    "data-vitality",
    "cancelled",
  );
  await expect(p.locator(".hp-action-relay")).toHaveCount(0);
  checks.push("stop still executes during receiving and latest neutral cancelled wins");
  await scene();
  await p.locator(".hp-run-detail").first().click();
  await expect(p.locator(".hp-run-result")).toBeVisible();
  await p.evaluate(() => {
    const elements = [...document.querySelectorAll(".hp-inspector *")].filter((n) =>
      [...n.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim()),
    );
    const sizes = elements.map((n) => parseFloat(getComputedStyle(n).fontSize));
    elements.forEach((n, i) => (n.style.fontSize = sizes[i] * 2 + "px"));
  });
  await p.screenshot({ path: resolve(out, "frames/text-200.png") });
  const close = p.locator('.hp-inspector .hp-surface-header button[title="关闭"]');
  await expect(close).toBeInViewport();
  await close.click();
  checks.push("200% computed text fixture: header close remains in viewport and operable");
  await scene();
  const keyboardSource = p.locator(".hp-run-detail").nth(1);
  await keyboardSource.focus();
  await p.keyboard.press("Enter");
  await expect(p.locator("[data-inspector-phase]")).toHaveAttribute(
    "data-inspector-phase",
    "receiving",
  );
  await expect(p.locator(".hp-run-result")).toHaveText("执行 B：第二条结果，独立来源。");
  await p.locator('.hp-inspector .hp-surface-header button[title="关闭"]').click();
  await expect(keyboardSource).toBeFocused();
  checks.push("keyboard Enter opens from the actual B trigger and close restores that trigger");
  // Receiving focus can move to Composer; arrival must not steal it.
  await scene();
  await p.locator(".hp-run-detail").first().click();
  await p.getByLabel("消息输入").focus();
  await p.waitForTimeout(1100);
  await expect(p.getByLabel("消息输入")).toBeFocused();
  checks.push("arrival does not steal focus moved from neutral title to Composer");
  await p.goto("http://127.0.0.1:5319/REVIEW.html");
  const before = p.frameLocator("#before"),
    after = p.frameLocator("#after");
  await expect(before.locator(".hp-run-detail")).toHaveCount(3);
  await expect(after.locator(".hp-run-detail")).toHaveCount(3);
  await after.locator(".hp-run-detail").first().click();
  await expect(after.locator(".hp-inspector-relay")).toHaveCount(1);
  await expect(after.locator(".hp-run-result")).toHaveText("执行 A：第一条已核实的结果。");
  await before.locator(".hp-run-detail").first().click();
  await expect(before.locator(".hp-run-result")).toHaveText("执行 A：第一条已核实的结果。");
  expect(await p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await p.screenshot({ path: resolve(out, "frames/comparison.png") });
  checks.push(
    "same-data comparison keeps desktop iframe geometry and both production versions operate",
  );
  expect(errors).toEqual([]);
  console.log(checks.join("\n"));
} finally {
  await writeFile(resolve(out, "logs/combined.json"), JSON.stringify({ checks, errors }, null, 2));
  await c.close();
  await b.close();
}
