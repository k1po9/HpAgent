/* global document, getComputedStyle, KeyboardEvent, CompositionEvent, Event */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
import { auditContrast } from "./r13-material-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-04");
const routes = JSON.parse(
  await readFile(resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-03/routes.json")),
);
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const login = await b.newContext({ reducedMotion: "reduce" }),
  p = await login.newPage();
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
const storage = await login.storageState();
await login.close();
const proof = {
  browser: await b.version(),
  results: [],
  errors: [],
  source:
    "real isolated API; upload and unknown-submit branches explicitly controlled; no provider/QQ external calls",
};
await mkdir(resolve(out, "screenshots"), { recursive: true });
for (const [version, port, width] of [
  ["before", 5313, 1680],
  ["after", 5294, 1680],
  ["after", 5294, 390],
]) {
  const folder = resolve(out, "clips", `${version}-${width}`);
  await mkdir(folder, { recursive: true });
  const c = await b.newContext({
    storageState: storage,
    viewport: { width, height: width === 390 ? 844 : 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
    recordVideo: { dir: folder, size: { width, height: width === 390 ? 844 : 1000 } },
  });
  const q = await c.newPage();
  q.setDefaultTimeout(10000);
  q.on("pageerror", (e) => proof.errors.push(e.message));
  await q.goto(`http://127.0.0.1:${port}/` + routes.ST03);
  await expect(q.locator(".hp-composer")).toBeVisible();
  await expect(q.locator(".hp-entry-origin")).toHaveCount(0);
  await q.emulateMedia({ reducedMotion: "no-preference" });
  await q.mouse.move(0, 0);
  const form = q.locator(".hp-composer"),
    input = q.getByLabel("消息输入"),
    send = q.getByRole("button", { name: "发送", exact: true });
  const events = [],
    zero = Date.now();
  const mark = (name) => events.push({ name, ms: Date.now() - zero });
  async function shot(name) {
    await q.waitForTimeout(400);
    await q.screenshot({ path: resolve(out, "screenshots", `${version}-${width}-${name}.png`) });
    const optics = await form.evaluate((n) => ({
      phase: n.dataset.actionPhase,
      seam: getComputedStyle(n, "::after").opacity,
      reflection: getComputedStyle(n, "::before").opacity,
      outline: getComputedStyle(n).outlineColor,
      buttonTransform: getComputedStyle(n.querySelector(".hp-composer__action")).transform,
      buttonType: n.querySelector(".hp-composer__action").getAttribute("type"),
    }));
    proof.results.push({ version, width, name, optics, contrast: await auditContrast(q) });
    console.log(version, width, name);
  }
  mark("idle");
  await shot("idle");
  mark("input focus");
  await input.click();
  await shot("focus");
  mark("draft ready");
  await input.fill("请整理评审要点，保留已有成果。");
  await q.mouse.move(0, 0);
  await shot("ready");
  if (version === "after") await expect(form).toHaveAttribute("data-action-phase", "ready");
  mark("pointer send intent");
  await send.hover();
  await shot("intent");
  if (version === "after") {
    await expect(form).toHaveAttribute("data-action-phase", "intent");
    const same = await send.getAttribute("type");
    expect(same).toBe("submit");
  }
  mark("pointer leaves");
  await q.mouse.move(0, 0);
  await shot("ready-return");
  mark("keyboard intent");
  await input.focus();
  for (let i = 0; i < 12; i++) {
    await q.keyboard.press("Tab");
    if (await send.evaluate((n) => n === document.activeElement)) break;
  }
  await expect(send).toBeFocused();
  await shot("keyboard");
  if (version === "after") {
    await expect(form).toHaveAttribute("data-action-phase", "intent");
    await q.emulateMedia({ reducedMotion: "reduce" });
    mark("system reduced");
    await shot("reduce");
    expect(await send.evaluate((n) => getComputedStyle(n).transform)).toBe("none");
    await q.emulateMedia({ reducedMotion: "no-preference" });
    await q.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(form).toHaveAttribute("data-action-motion", "false");
    await q.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(form).toHaveAttribute("data-action-phase", "ready");
    mark("visibility restored without replay");
    await input.focus();
    await input.evaluate((n) =>
      n.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true })),
    );
    await expect(form).toHaveAttribute("data-action-phase", "composing");
    await input.evaluate((n) =>
      n.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          keyCode: 229,
          isComposing: true,
          bubbles: true,
        }),
      ),
    );
    await expect(input).toHaveValue("请整理评审要点，保留已有成果。");
    await shot("ime");
    await input.evaluate((n) =>
      n.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true })),
    );
    if (width === 390) {
      await send.dispatchEvent("pointerover", { pointerType: "touch" });
      await expect(form).toHaveAttribute("data-action-phase", "ready");
      await shot("touch-no-hover");
    }
  }
  // Commit once via original product form. Unknown response is delayed and rejected only in this test.
  let finish;
  const latch = new Promise((r) => {
    finish = r;
  });
  await q.route("**/api/v1/conversations/*/messages", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await latch;
    await route.fulfill({
      status: 503,
      json: { error: { code: "temporarily_unavailable", message: "评审受控确认未知" } },
    });
  });
  mark("submit begins");
  await send.click();
  if (version === "after") await expect(form).toHaveAttribute("data-action-phase", "submitting");
  await shot("submitting");
  await input.fill("提交未确认期间保留的新草稿");
  finish();
  await expect(q.getByRole("button", { name: "用原提交确认上次发送" })).toBeVisible();
  if (version === "after") await expect(form).toHaveAttribute("data-action-phase", "uncertain");
  await shot("uncertain");
  await expect(input).toHaveValue("提交未确认期间保留的新草稿");
  const video = q.video();
  await c.close();
  await video.saveAs(resolve(out, "clips", `${version}-${width}.webm`));
  proof.results.push({ version, width, events, video: `clips/${version}-${width}.webm` });
  await writeFile(resolve(out, "logs/input-browser.json"), JSON.stringify(proof, null, 2));
}
await b.close();
