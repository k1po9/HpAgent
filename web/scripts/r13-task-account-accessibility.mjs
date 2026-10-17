import "./review-runtime.mjs";
/* global innerWidth, innerHeight, outerWidth, devicePixelRatio, visualViewport, getComputedStyle, document */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Buffer } from "node:buffer";
import console from "node:console";
import process from "node:process";
import { auditContrast } from "./r13-material-contrast.mjs";
const stage = process.argv[2] ?? "accessibility";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06"),
  dir = resolve(out, "screenshots", stage);
await mkdir(dir, { recursive: true });
const routes = JSON.parse(await readFile(resolve(out, "routes.json"))),
  proof = [],
  results = [];
for (const [name, zoom, font] of [
  ["normal", 1, 16],
  ["browser200", 2, 16],
  ["browserFont32", 1, 32],
]) {
  const profile = await mkdtemp(join(tmpdir(), "hpagent-r13-accessibility-"));
  await mkdir(join(profile, "Default"));
  const pref = {
    partition: { default_zoom_level: { x: Math.log(zoom) / Math.log(1.2) } },
    webkit: { webprefs: { default_font_size: font } },
  };
  await writeFile(join(profile, "Default", "Preferences"), JSON.stringify(pref));
  const c = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
      headless: true,
      viewport: null,
      args: ["--window-size=1680,1000"],
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
      reducedMotion: "reduce",
    }),
    p = c.pages()[0];
  async function settled() {
    await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
    await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
    await p.evaluate(() => document.fonts.ready);
  }
  async function shot(scene) {
    const m = await p.evaluate(() => ({
      width: innerWidth,
      height: innerHeight,
      outer: outerWidth,
      dpr: devicePixelRatio,
      pinchScale: visualViewport.scale,
      documentWidth: document.documentElement.scrollWidth,
      rootFont: getComputedStyle(document.documentElement).fontSize,
      inputFont: document.querySelector(".hp-composer__input")
        ? getComputedStyle(document.querySelector(".hp-composer__input")).fontSize
        : null,
    }));
    expect(m.documentWidth).toBeLessThanOrEqual(m.width + 1);
    expect(m.dpr).toBe(zoom);
    expect(m.pinchScale).toBe(1);
    const session = await p.context().newCDPSession(p);
    const capture = await session.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
      fromSurface: true,
    });
    await writeFile(resolve(dir, name + "-" + scene + ".png"), Buffer.from(capture.data, "base64"));
    // Browser context closes this read-only capture session after the scenario set.
    proof.push({ name, scene, prefs: pref, metrics: m, contrast: await auditContrast(p) });
  }
  try {
    await p.goto("http://127.0.0.1:5294/");
    await p.getByLabel("用户名").fill("r1_review");
    await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
    await p.getByRole("button", { name: "登录", exact: true }).focus();
    await p.keyboard.press("Enter");
    await expect(p.locator(".hp-shell")).toBeVisible();
    await settled();
    for (const scene of ["ST02", "ST03", "ST07", "artifact"]) {
      console.log("scene", name, scene);
      await p.goto("http://127.0.0.1:5294/" + routes[scene]);
      await expect(p.locator(".hp-shell")).toBeVisible();
      await settled();
      if (scene === "ST07") await expect(p.locator(".hp-inspector-card")).toBeVisible();
      if (scene === "artifact")
        await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
      await p.locator(".hp-thread__viewport").evaluate((e) => (e.scrollTop = 0));
      await shot(scene);
    }
    await p.goto("http://127.0.0.1:5294/" + routes.ST02);
    await settled();
    await p.getByRole("button", { name: "账户设置", exact: true }).click();
    await expect(p.getByRole("dialog", { name: "账户设置", exact: true })).toBeVisible();
    await expect(p.getByRole("button", { name: "绑定 QQ", exact: true })).toBeVisible();
    await shot("account");
    await p.keyboard.press("Escape");
    await expect(p.getByRole("button", { name: "账户设置", exact: true })).toBeFocused();
    await p.goto("http://127.0.0.1:5294/#/tasks?bucket=attention&type=all");
    await settled();
    await expect(p.locator(".hp-task-lane")).toHaveCount(4);
    await expect(p.locator(".hp-task-sync")).toContainText("本轮同步完成");
    await expect(p.getByRole("button", { name: "新建任务", exact: true })).toBeInViewport();
    await shot("tasks");
    await p.goto("http://127.0.0.1:5294/" + routes.ST02);
    await settled();
    const draft = p.getByLabel("消息输入");
    await draft.fill("缩放与浏览器字体设置下的中文草稿");
    await expect(p.getByRole("button", { name: "发送", exact: true })).toBeVisible();
    await shot("draft-focus");
    await p.locator(".hp-artifact-message-card").first().focus();
    await p.keyboard.press("Enter");
    await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
    await p.getByRole("button", { name: "保存源码副本到空间" }).focus();
    await p.keyboard.press("Enter");
    await expect(p.getByRole("dialog")).toBeVisible();
    const confirm = p.getByRole("button", { name: "确认保存", exact: true });
    await expect(confirm).toBeEnabled();
    await confirm.focus();
    await expect(confirm).toBeFocused();
    await expect(confirm).toBeVisible();
    const box = await confirm.boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(await p.evaluate(() => innerHeight));
    await shot("nested-Surface-actions");
    await p.keyboard.press("Escape");
    await expect(p.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
    await p.getByRole("button", { name: "关闭HTML 成果" }).focus();
    await p.keyboard.press("Enter");
    await expect(draft).toHaveValue("缩放与浏览器字体设置下的中文草稿");
    results.push({ name, result: "pass" });
    console.log("PASS", name);
  } finally {
    await c.close();
    await rm(profile, { recursive: true, force: true });
    await writeFile(
      resolve(out, "logs/" + stage + ".json"),
      JSON.stringify(
        {
          results,
          proof,
          scope:
            "Native Chromium page zoom in isolated profile. Font32 changes browser default font, fixed px application text intentionally remains fixed; not a universal text enlargement.",
        },
        null,
        2,
      ),
    );
  }
}
const normal = proof.find((x) => x.name === "normal"),
  zoom = proof.find((x) => x.name === "browser200");
expect(normal.metrics.width / zoom.metrics.width).toBe(2);
