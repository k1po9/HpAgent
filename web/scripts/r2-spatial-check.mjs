import "./review-runtime.mjs";
/* global document, window, innerWidth, Event */
import console from "node:console";
import process from "node:process";
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
if (!process.argv.includes("--isolated"))
  throw Error("Verify API8197 isolated DB/Redis/files first");
const batch = process.argv[2] ?? "initial";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-01");
const dir = resolve(out, "screenshots", batch);
await mkdir(dir, { recursive: true });
const routes = JSON.parse(
  await readFile(resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/routes.json")),
);
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const ctx = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
  recordVideo: { dir: resolve(out, "videos", batch), size: { width: 1680, height: 1000 } },
});
const p = await ctx.newPage(),
  errors = [],
  results = [],
  events = [];
p.on("pageerror", (e) => errors.push(e.message));
let start = Date.now();
const log = (name) => events.push({ name, ms: Date.now() - start });
async function shot(name) {
  await p.screenshot({ path: resolve(dir, name + ".png") });
}
async function wait() {
  await p.waitForTimeout(500);
}
async function check(name, fn) {
  log(name);
  try {
    await fn();
    results.push({ name, result: "pass" });
  } catch (e) {
    results.push({ name, result: "fail", error: e.message });
    await shot("failure-" + name);
    throw e;
  }
}
const nav = () => p.getByRole("navigation", { name: "主导航" });
async function go(name) {
  await nav().getByRole("button", { name, exact: true }).click();
}
async function goto(scene) {
  await p.goto("http://127.0.0.1:5294/" + (routes[scene] ?? scene));
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  await wait();
}
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  await p.emulateMedia({ reducedMotion: "no-preference" });
  start = Date.now();
  await check("B01-conversation", async () => {
    await goto("ST02");
    const input = p.getByLabel("消息输入");
    await input.fill("R2 草稿保位");
    await p.locator(".hp-thread__viewport").evaluate((el) => (el.scrollTop = 0));
    const source = routes.ST02.split("/").at(-1),
      target = routes.ST03.split("/").at(-1);
    await p.locator(`[data-conversation-id="${target}"]`).focus();
    await p.keyboard.press("Enter");
    await expect(p.locator(".hp-conv--active")).toHaveAttribute("data-conversation-id", target);
    await shot("conversation-start");
    await wait();
    await shot("conversation-end");
    await p.locator(`[data-conversation-id="${source}"]`).click();
    await expect(input).toHaveValue("R2 草稿保位");
    await input.fill("");
  });
  await check("B02-shift", async () => {
    await go("空间");
    await shot("rail-start");
    await p.waitForTimeout(100);
    await shot("rail-middle");
    await wait();
    await shot("rail-end");
    for (let i = 0; i < 20; i++) {
      await go(["任务", "AI", "空间"][i % 3]);
      await p.waitForTimeout(60);
    }
    await go("AI");
    await wait();
    await expect(p.locator(".hp-rail-track")).toHaveCount(1);
    await expect(nav().getByRole("button", { name: "AI", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(await p.locator(".hp-rail-track").evaluate((el) => el.getAnimations().length)).toBe(0);
  });
  await check("B02-guard", async () => {
    await go("任务");
    await wait();
    await p.getByRole("button", { name: "新建任务", exact: true }).click();
    await p.getByLabel("工作名称").fill("R2 未提交草稿");
    await p.keyboard.press("Escape");
    await expect(
      p.getByRole("dialog", { name: "放弃未提交的任务草稿？", exact: true }),
    ).toBeVisible();
    await p.keyboard.press("Escape");
    await expect(p.getByLabel("工作名称")).toHaveValue("R2 未提交草稿");
    // Native top layer prevents background Rail clicks; hash navigation exercises the real guard.
    await p.evaluate(() => {
      window.location.hash = "#/workspace";
    });
    await expect(
      p.getByRole("dialog", { name: "放弃未提交的任务草稿？", exact: true }),
    ).toBeVisible();
    await expect(nav().getByRole("button", { name: "任务", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await shot("guard-blocked");
    await p.getByRole("button", { name: "继续编辑", exact: true }).click();
    await expect(p.getByLabel("工作名称")).toHaveValue("R2 未提交草稿");
    await p.keyboard.press("Escape");
    await p.getByRole("button", { name: "放弃草稿并关闭", exact: true }).click();
    await wait();
  });
  await check("B04-elevate", async () => {
    await p.getByRole("button", { name: "账户设置", exact: true }).click();
    await shot("surface-start");
    await p.waitForTimeout(90);
    await shot("surface-middle");
    await wait();
    await shot("surface-end");
    await p.keyboard.press("Escape");
    await expect(p.getByRole("dialog", { name: "账户设置", exact: true })).toHaveCount(0);
    await expect(p.getByRole("button", { name: "账户设置", exact: true })).toBeFocused();
    await shot("surface-retreat");
    await wait();
    await expect(p.locator(".hp-surface-retreat")).toHaveCount(0);
    for (let i = 0; i < 20; i++) {
      await p.getByRole("button", { name: "账户设置", exact: true }).click();
      await p.waitForTimeout(60);
      await p.keyboard.press("Escape");
    }
    await wait();
    await expect(p.locator(".hp-surface-retreat")).toHaveCount(0);
  });
  await check("B05-inspector", async () => {
    await goto("artifact");
    await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
    await p.getByTitle("Artifact 预览").evaluate((el) => (window.r2Frame = el));
    await p
      .frameLocator("iframe")
      .locator("body")
      .evaluate((el) => (el.dataset.r2Marker = "same"));
    await p.getByLabel("修改指令").fill("R2 iframe 草稿");
    expect(await p.locator(".hp-inspector").getAttribute("data-spatial-motion")).toBe("none");
    for (const width of [1280, 1279, 780, 390, 1680]) {
      await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await wait();
      expect(await p.getByTitle("Artifact 预览").evaluate((el) => el === window.r2Frame)).toBe(
        true,
      );
      await expect(p.getByLabel("修改指令")).toHaveValue("R2 iframe 草稿");
    }
    await p.setViewportSize({ width: 390, height: 844 });
    await p.getByRole("button", { name: "保存源码副本到空间" }).click();
    await expect(p.locator(".hp-inspector")).toHaveAttribute("hidden", "");
    await wait();
    await shot("nested-mobile");
    await p.keyboard.press("Escape");
    await expect(p.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
    await expect(p.frameLocator("iframe").locator("body")).toHaveAttribute(
      "data-r2-marker",
      "same",
    );
    await p.keyboard.press("Escape");
    await expect(p.locator(".hp-inspector")).toHaveCount(0);
  });
  await check("X01-motion", async () => {
    await p.setViewportSize({ width: 1680, height: 1000 });
    await goto("user");
    await go("空间");
    await p.emulateMedia({ reducedMotion: "reduce" });
    await expect(p.locator(".hp-shell")).toHaveAttribute("data-spatial-motion", "reduced");
    expect(await p.locator(".hp-rail-track").evaluate((el) => el.getAnimations().length)).toBe(0);
    await p.getByRole("button", { name: "账户设置", exact: true }).click();
    expect(
      await p
        .getByRole("dialog", { name: "账户设置", exact: true })
        .evaluate((el) => el.getAnimations().length),
    ).toBe(0);
    await p.getByRole("checkbox", { name: /减少动态效果/ }).check();
    await p.emulateMedia({ reducedMotion: "no-preference" });
    await expect(p.locator(".hp-shell")).toHaveAttribute("data-spatial-motion", "reduced");
    await shot("local-reduced");
    await p.getByRole("checkbox", { name: /减少动态效果/ }).uncheck();
    expect(
      await p
        .getByRole("dialog", { name: "账户设置", exact: true })
        .evaluate((el) => el.getAnimations().length),
    ).toBe(0);
    await p.keyboard.press("Escape");
    await go("任务");
    await p.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await p.locator(".hp-rail-track").evaluate((el) => el.getAnimations().length)).toBe(0);
    await p.evaluate(() => {
      delete document.hidden;
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await wait();
    expect(await p.locator(".hp-rail-track").evaluate((el) => el.getAnimations().length)).toBe(0);
  });
  await check("B03-responsive", async () => {
    for (const width of [1680, 1280, 1279, 780, 390]) {
      await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await goto("user");
      await shot(`ai-${width}`);
      await go("任务");
      await wait();
      await shot(`tasks-${width}`);
      const geom = await p.evaluate(() => ({
        viewport: innerWidth,
        doc: document.documentElement.scrollWidth,
        track: (() => {
          const r = document.querySelector(".hp-rail-track").getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        })(),
      }));
      expect(geom.doc).toBeLessThanOrEqual(width);
      expect(width < 600 ? geom.track.height : geom.track.width).toBe(3);
      results.push({ name: `geometry-${width}`, result: "pass", geom });
      await p.getByRole("button", { name: "账户设置", exact: true }).click();
      await wait();
      await shot(`account-${width}`);
      await p.keyboard.press("Escape");
    }
    await p.setViewportSize({ width: 390, height: 568 });
    await goto("user");
    await p.getByRole("button", { name: "账户设置", exact: true }).click();
    await wait();
    await shot("mobile-short");
    await p.keyboard.press("Escape");
  });
  await check("X02-settled", async () => {
    await p.setViewportSize({ width: 1680, height: 1000 });
    await goto("user");
    await wait();
    results.push({
      name: "settled-motion-counts",
      result: "pass",
      metrics: await p.evaluate(() => ({
        tracks: document.querySelectorAll(".hp-rail-track").length,
        retreats: document.querySelectorAll(".hp-surface-retreat").length,
        animations: document
          .getAnimations()
          .filter((a) => a.playState === "running")
          .map((a) => a.effect?.target?.className),
      })),
    });
  });
  expect(errors).toEqual([]);
} catch (e) {
  console.error(e);
  process.exitCode = 1;
} finally {
  await writeFile(
    resolve(out, `logs/spatial-${batch}.json`),
    JSON.stringify(
      {
        browser: await b.version(),
        data: "real isolated API; hidden lifecycle uses explicit deterministic document.hidden injection",
        results,
        errors,
        events,
      },
      null,
      2,
    ),
  );
  await ctx.close();
  await b.close();
}
