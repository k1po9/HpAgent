/* global window, document, performance, requestAnimationFrame, location, setTimeout, console, process */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const reports = [],
  errors = [];
for (const mode of [
  "arrival",
  "reduce-during-travel",
  "rapid-late-response",
  "repeat-close",
  "zoom-200",
]) {
  const p = await browser.newPage({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "no-preference",
  });
  p.on("pageerror", (e) => errors.push(e.message));
  p.setDefaultTimeout(9000);
  try {
    await p.goto(
      pathToFileURL(out + "/offline/AFTER.React.Standalone.html").href +
        "#/ai/01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
    );
    await expect(p.locator(".hp-run-detail")).toHaveCount(3);
    await p.locator(".hp-thread__viewport").evaluate((n) => (n.scrollTop = 0));
    await expect(p.locator("#artifact-open-r42-A")).toHaveCount(1);
    await p.locator(".hp-thread__viewport").evaluate((n) => (n.scrollTop = n.scrollHeight));
    await expect(p.locator("#artifact-open-r42-C")).toHaveCount(1);
    await p.locator("#artifact-open-r42-B").evaluate((n) => n.scrollIntoView({ block: "center" }));
    await p.waitForTimeout(400);
    await p.evaluate(() => {
      window.migrationFrames = [];
      const start = performance.now();
      function sample() {
        const gate = document.querySelector("[data-inspector-gate]");
        window.migrationFrames.push({
          t: performance.now(),
          title: !!document.querySelector(".hp-inspector-title-arrival"),
          content: !!gate && !gate.hidden,
          relay: !!document.querySelector(".hp-inspector-relay"),
          exit: !!document.querySelector(".hp-inspector-exit"),
        });
        if (performance.now() - start < 30000) requestAnimationFrame(sample);
      }
      sample();
    });
    const ready = async (id) => {
      await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
      await expect(p.frameLocator('iframe[title="Artifact 预览"]').locator("#version")).toHaveText(
        `r42-${id} · v2`,
      );
    };
    if (mode === "rapid-late-response") {
      await p.evaluate(() => {
        const original = window.fetch;
        window.fetch = async (...args) => {
          const url = String(args[0]);
          if (/artifacts\/r42-[AB]/.test(url))
            await new Promise((r) => setTimeout(r, url.includes("r42-A") ? 1600 : 900));
          return original(...args);
        };
      });
      for (const id of ["A", "B", "C"]) await p.locator("#artifact-open-r42-" + id).click();
      await ready("C");
      await p.waitForTimeout(1800);
      await ready("C");
      expect(await p.evaluate(() => location.hash)).toContain("r42-C");
    } else {
      await p.locator("#artifact-open-r42-B").click();
      if (mode === "arrival" || mode === "reduce-during-travel") {
        await expect(p.locator(".hp-inspector-relay")).toHaveCount(1);
        await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
        await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("inert");
        await p.screenshot({ path: `${out}/screenshots/interactions/${mode}-travel.png` });
        if (mode === "reduce-during-travel") await p.emulateMedia({ reducedMotion: "reduce" });
      }
      await ready("B");
      if (mode === "arrival") {
        const frames = await p.evaluate(() => window.migrationFrames);
        const title = frames.find((f) => f.title),
          content = frames.find((f) => f.content);
        expect(title).toBeTruthy();
        expect(content.t - title.t).toBeGreaterThan(140);
        expect(
          await p.evaluate(() => window.__r4events.some((e) => e.reason === "actual-tip")),
        ).toBe(true);
        const count = await p.evaluate(() => window.__r4events.length);
        await p.getByRole("tab", { name: "预览", exact: true }).focus();
        await p.keyboard.press("ArrowRight");
        await expect(p.getByRole("tab", { name: "版本历史", exact: true })).toBeFocused();
        await p.keyboard.press("End");
        await expect(p.getByRole("tab", { name: "详情", exact: true })).toBeFocused();
        await p.keyboard.press("Home");
        await p.locator(".hp-inspector-expand").click();
        expect(await p.evaluate(() => window.__r4events.length)).toBe(count);
      }
      if (mode === "zoom-200") {
        await p.evaluate(() => (document.documentElement.style.zoom = "2"));
        await expect(p.locator('.hp-inspector button[title="关闭"]')).toBeInViewport();
        await expect(p.getByLabel("修改指令")).toBeVisible();
      }
      if (mode === "repeat-close") {
        await p.emulateMedia({ reducedMotion: "reduce" });
        for (let i = 0; i < 20; i++) {
          await p.locator('.hp-inspector button[title="关闭"]').click();
          await expect(p.locator(".hp-inspector")).toHaveCount(0);
          await expect(p.locator(".hp-capture-dual")).toHaveCount(0);
          await p.locator("#artifact-open-r42-B").click();
          await ready("B");
          await expect(p.locator(".hp-capture-dual")).toHaveCount(1);
        }
      }
      if (mode !== "zoom-200") {
        await p.locator('.hp-inspector button[title="关闭"]').click();
        await expect(p.locator(".hp-inspector")).toHaveCount(0);
        expect(
          await p
            .locator(".hp-inspector-exit")
            .evaluateAll((nodes) => nodes.every((n) => n.inert && !n.textContent.trim())),
        ).toBe(true);
        await expect(p.locator("#artifact-open-r42-B")).toBeFocused();
        await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
      }
    }
    reports.push({ mode, result: "pass" });
  } catch (e) {
    reports.push({ mode, result: "fail", error: String(e) });
  }
  await p.screenshot({ path: `${out}/screenshots/interactions/motion-${mode}.png` });
  await writeFile(
    `${out}/evidence/motion-${mode}.json`,
    JSON.stringify(
      await p.evaluate(() => ({
        frames: window.migrationFrames,
        events: window.__r4events,
        requests: window.__handoffLog,
        timeline: window.__r4log,
      })),
      null,
      2,
    ),
  );
  await p.close();
}
await browser.close();
await writeFile(
  `${out}/evidence/motion-regression.json`,
  JSON.stringify({ reports, errors }, null, 2),
);
console.log(JSON.stringify({ reports, errors }, null, 2));
if (reports.some((r) => r.result === "fail") || errors.length) process.exitCode = 1;
