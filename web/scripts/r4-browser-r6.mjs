import console from "node:console";
/* global window, document, Event, localStorage, setTimeout, innerWidth, innerHeight */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-06");
const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const context = await browser.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "no-preference",
});
const page = await context.newPage(),
  errors = [],
  checks = [],
  scenes = {};
page.on("pageerror", (e) => errors.push(e.message));
page.setDefaultTimeout(12000);
const note = (s) => {
  checks.push(s);
  console.log(s);
};
async function scene(name = "normal") {
  await page.goto("http://127.0.0.1:5323/lab.html?case=" + name);
  if (await page.getByLabel("用户名").isVisible()) {
    await page.getByLabel("用户名").fill("r1_review");
    await page.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
    await page.getByRole("button", { name: "登录", exact: true }).click();
  }
  await expect(page.locator(".hp-run-detail")).toHaveCount(3, { timeout: 25000 });
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 25000 });
  await page.waitForTimeout(300);
}
const open = async (i) => page.locator(".hp-run-detail").nth(i).click();
const close = async () =>
  page.locator('.hp-inspector .hp-surface-header button[title="关闭"]').click();
const phase = () => page.locator("[data-inspector-phase]");
try {
  await scene();
  await page
    .locator(".hp-run-detail")
    .first()
    .evaluate((n) => {
      window.__source = n;
    });
  await open(0);
  await page.waitForTimeout(250);
  await expect(phase()).toHaveAttribute("data-inspector-phase", "receiving");
  await expect(page.getByRole("complementary", { name: "接纳中" })).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.locator("[data-inspector-gate]")).toHaveAttribute("hidden", "");
  await expect(page.locator("[data-inspector-gate]")).toHaveAttribute("inert", "");
  await expect(page.locator(".hp-inspector-relay")).toHaveCount(1);
  await page.screenshot({ path: resolve(out, "frames/receiving.png") });
  await expect(page.locator(".hp-run-result")).toBeVisible();
  await expect(phase()).toHaveAttribute("data-inspector-phase", "settled");
  expect(
    await page
      .locator(".hp-run-detail")
      .first()
      .evaluate((n) => n === window.__source),
  ).toBe(true);
  await page.screenshot({ path: resolve(out, "frames/landed.png") });
  scenes.normal = await page.evaluate(() => window.__r4log);
  const frames = scenes.normal.filter((x) => x.type === "frame" && x.head !== undefined);
  expect(
    frames.some((x) => x.phase === "receiving" && Number(x.head) < Number(x.length) - 0.5),
  ).toBe(true);
  expect(
    frames
      .filter((x) => x.phase === "landed")
      .every((x) => Number(x.head) >= Number(x.length) - 0.5),
  ).toBe(true);
  const response = scenes.normal.find((x) => x.type === "response" && x.id === "r4-A"),
    landed = scenes.normal.find((x) => x.phase === "landed");
  expect(response.t).toBeLessThan(landed.t);
  note(
    "I01/I02/I03/I05: query and fast response before actual SVG tip arrival; neutral accessible gate; original DOM preserved",
  );
  const mounts = await page.locator(".hp-run-inspector").evaluate((n) => {
    window.__runNode = n;
    return true;
  });
  expect(mounts).toBe(true);
  await page.getByRole("tab", { name: "概览", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "使用资料与输出" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(page.url()).toContain("tab=resources");
  await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
  expect(await page.locator(".hp-run-inspector").evaluate((n) => n === window.__runNode)).toBe(
    true,
  );
  await page.getByRole("button", { name: "扩大阅读" }).click();
  await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
  await page.getByRole("button", { name: "恢复宽度" }).click();
  note("I10: keyboard Tab/hash/ARIA and expanded preserve Run DOM and do not replay");
  await close();
  await expect(page.locator(".hp-inspector")).toHaveCount(0);
  expect(page.url()).not.toContain("inspect=");
  await expect(page.locator(".hp-inspector-exit")).toHaveCount(1);
  expect(await page.locator(".hp-inspector-exit").textContent()).toBe("");
  await expect(page.locator(".hp-inspector-exit")).toHaveAttribute("inert", "");
  expect(await page.evaluate(() => document.activeElement?.id)).toBe("run-open-r4-A");
  await page.screenshot({ path: resolve(out, "frames/closing.png") });
  await expect(page.locator(".hp-inspector-exit")).toHaveCount(0);
  note(
    "I06/I08: X clears route and content, restores origin focus; blank inert shell releases after 680ms",
  );
  await open(1);
  await expect(page.locator(".hp-run-result")).toBeVisible();
  await close();
  await page.waitForTimeout(150);
  await open(2);
  await page.waitForTimeout(900);
  await expect(page.locator(".hp-run-result")).toHaveText("执行 C：最后选择的当前结果。");
  await expect(page.locator(".hp-inspector-exit")).toHaveCount(0);
  note("I09: close then reopen cancels old exit and timer");
  await scene("fast-switch");
  await page.evaluate(() => {
    [0, 1, 2].forEach((i) =>
      setTimeout(() => document.querySelectorAll(".hp-run-detail")[i].click(), i * 160),
    );
  });
  await page.waitForTimeout(2300);
  await expect(page.locator(".hp-run-result")).toHaveText("执行 C：最后选择的当前结果。");
  scenes.fastSwitch = await page.evaluate(() => window.__r4log);
  note("I04: A→B→C with A/B network late callbacks only reveals C");
  await scene("slow");
  await open(0);
  await expect(phase()).toHaveAttribute("data-inspector-phase", /landed|settled/);
  await expect(page.getByText("正在加载执行…", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(out, "frames/slow-loading.png") });
  await expect(page.locator(".hp-run-result")).toBeVisible();
  scenes.slow = await page.evaluate(() => window.__r4log);
  expect(scenes.slow.find((x) => x.phase === "landed").t).toBeLessThan(
    scenes.slow.find((x) => x.type === "response").t,
  );
  note("I03 slow: actual arrival exposes current loading, late data fills same target");
  for (const name of ["denied", "offline"]) {
    await scene(name);
    await open(0);
    await expect(
      page.getByText(name === "denied" ? "对象不可用。" : "待同步，请重试。", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
    scenes[name] = await page.evaluate(() => window.__r4log);
  }
  note("I03 errors: 403/offline settle safely without old data or stuck gate");
  await scene();
  await open(0);
  await page.waitForTimeout(180);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(phase()).toHaveAttribute("data-inspector-phase", "settled");
  await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
  await close();
  await expect(page.locator(".hp-inspector-exit")).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await open(0);
  await page.waitForTimeout(180);
  await page.evaluate(() => {
    localStorage.setItem("hpagent.motion", "reduce");
    window.dispatchEvent(new Event("storage"));
  });
  await expect(phase()).toHaveAttribute("data-inspector-phase", "settled");
  await close();
  await page.evaluate(() => {
    localStorage.removeItem("hpagent.motion");
    window.dispatchEvent(new Event("storage"));
  });
  note("I14: system and local reduced motion during travel settle; close immediate; no replay");
  for (const interruption of ["scroll", "hidden", "resize", "source-loss"]) {
    await close().catch(() => {});
    await page.waitForTimeout(750);
    await open(0);
    await page.waitForTimeout(150);
    if (interruption === "resize") {
      await page.setViewportSize({ width: 1640, height: 1000 });
    } else
      await page.evaluate((kind) => {
        if (kind === "scroll") window.dispatchEvent(new Event("scroll"));
        if (kind === "source-loss")
          document.getElementById("run-open-r4-A").style.visibility = "hidden";
        if (kind === "hidden") {
          Object.defineProperty(document, "hidden", { configurable: true, value: true });
          document.dispatchEvent(new Event("visibilitychange"));
        }
      }, interruption);
    await expect(phase()).toHaveAttribute("data-inspector-phase", "settled");
    await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
    await page.evaluate(() => {
      document.getElementById("run-open-r4-A").style.visibility = "";
      Object.defineProperty(document, "hidden", { configurable: true, value: false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
  note("I13/I14: scroll, hidden, resize and source loss directly settle");
  const geometry = [];
  for (const [width, height] of [
    [390, 844],
    [780, 900],
    [1279, 900],
    [1280, 900],
    [1680, 1000],
    [1680, 620],
  ]) {
    await page.setViewportSize({ width, height });
    await scene();
    await open(0);
    await expect(
      page.getByRole(width < 1280 ? "dialog" : "complementary", { name: "执行详情" }),
    ).toBeVisible();
    if (width < 1280) {
      await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => !!document.activeElement.closest(".hp-inspector"))).toBe(
        true,
      );
    }
    await page.screenshot({ path: resolve(out, `frames/viewport-${width}-${height}.png`) });
    geometry.push(
      await page.locator(".hp-inspector").evaluate((n) => {
        const r = n.getBoundingClientRect();
        return {
          width: innerWidth,
          height: innerHeight,
          x: r.x,
          y: r.y,
          w: r.width,
          h: r.height,
          modal: n.getAttribute("aria-modal"),
          focus: document.activeElement?.id,
        };
      }),
    );
    await page.keyboard.press("Escape");
    await expect(page.locator(".hp-inspector")).toHaveCount(0);
    await expect(page.locator(".hp-inspector-exit")).toHaveCount(0);
  }
  note("I15/I06: 390/780/1279 native modal, 1280/1680 sidebar and short screen; Escape/focus");
  scenes.geometry = geometry;
  await page.setViewportSize({ width: 1680, height: 1000 });
  await scene();
  await open(0);
  await expect(page.locator(".hp-run-result")).toBeVisible();
  await page.getByRole("button", { name: "账户设置" }).click();
  const account = page.getByRole("dialog", { name: "账户设置" });
  await expect(account).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(account).toHaveCount(0);
  await expect(page.locator(".hp-inspector")).toHaveCount(1);
  await close();
  note("I16: account Surface is highest layer; Escape keeps Inspector");
  await scene();
  await page.getByLabel("消息输入").fill("R4组合：接受后接力时打开 Run");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".hp-action-relay")).toHaveCount(1);
  await page.locator("#run-open-r4-send").click();
  await expect(page.locator(".hp-action-relay")).toHaveCount(0);
  await expect(page.locator(".hp-inspector")).toBeVisible();
  await expect(page.locator(".hp-inspector .hp-vitality")).toHaveAttribute(
    "data-vitality",
    "waiting",
  );
  await close();
  await page.waitForTimeout(800);
  await expect(page.locator(".hp-action-relay")).toHaveCount(0);
  note(
    "I17: R3 travelling cancelled by actual navigation; queued C remains waiting; close has no replay",
  );
  await scene();
  await open(0);
  await expect(phase()).toHaveAttribute("data-inspector-phase", "settled");
  await close();
  await page.waitForTimeout(800);
  for (let i = 0; i < 20; i++) {
    await open(i % 3);
    await page.waitForTimeout(60);
    await close();
    await page.waitForTimeout(60);
  }
  await page.waitForTimeout(1000);
  await expect(page.locator(".hp-inspector-relay,.hp-inspector-exit,.hp-action-relay")).toHaveCount(
    0,
  );
  note("I19: 20 opens/close interrupts leave no overlay or blank shell");
  await page.goto("http://127.0.0.1:5323/focus.html");
  await expect(page.frameLocator("#lab").locator(".hp-run-detail")).toHaveCount(3);
  await page.getByRole("button", { name: "A→B→C 快切" }).click();
  await expect(page.frameLocator("#lab").locator(".hp-run-result")).toHaveText(
    "执行 C：最后选择的当前结果。",
  );
  await page.getByRole("button", { name: "关闭后 200ms 重开" }).click();
  await expect(page.frameLocator("#lab").locator(".hp-run-result")).toHaveText(
    "执行 A：第一条已核实的结果。",
  );
  note("review focus page controls operate production React");
  expect(errors).toEqual([]);
} catch (e) {
  await page.screenshot({ path: resolve(out, "frames/failure.png") });
  throw e;
} finally {
  await writeFile(
    resolve(out, "logs/browser.json"),
    JSON.stringify(
      {
        checks,
        errors,
        scenes,
        browser: browser.version(),
        viewport: "matrix",
        data: "explicit browser account/API/SSE fixtures; production React",
      },
      null,
      2,
    ),
  );
  await context.close();
  await browser.close();
}
