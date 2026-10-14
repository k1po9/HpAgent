import console from "node:console";
/* global document, window, getComputedStyle, performance, HTMLElement, DOMRect, innerWidth */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-06");
const b = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  }),
  reports = [],
  errors = [];
try {
  for (const [scene, width, edge] of [
    ["normal", 1680, false],
    ["slow", 1680, false],
    ["normal", 3840, false],
    ["normal", 1680, true],
  ]) {
    const p = await b.newPage({
      viewport: { width, height: width === 3840 ? 2160 : 1000 },
      reducedMotion: "no-preference",
    });
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto("http://127.0.0.1:5323/lab.html?case=" + scene);
    await expect(p.locator(".hp-run-detail")).toHaveCount(3);
    await p.waitForTimeout(400);
    const source = p.locator(".hp-run-detail").nth(1);
    const layout = await source.evaluate((n) => {
      const group = n.closest(".hp-answer-surface"),
        text = group.querySelector(".hp-text-part"),
        card = n.getBoundingClientRect(),
        r = text.getBoundingClientRect();
      return {
        groupClass: group.className,
        groupBackground: getComputedStyle(group).backgroundColor,
        groupBorder: getComputedStyle(group).borderTopWidth,
        cardBackground: getComputedStyle(n).backgroundColor,
        cardBorder: getComputedStyle(n).borderTopWidth,
        radius: getComputedStyle(n).borderRadius,
        textBottom: r.bottom,
        cardTop: card.top,
        inside: !!text.closest(".hp-execution-block"),
        anchor: !n.querySelector("[data-relay-anchor]"),
      };
    });
    expect(layout.groupClass).toContain("hp-answer-surface--run");
    expect(layout.groupBackground).toBe("rgba(0, 0, 0, 0)");
    expect(layout.groupBorder).toBe("0px");
    expect(layout.cardBackground).toBe("rgb(255, 255, 255)");
    expect(layout.cardBorder).toBe("1px");
    expect(layout.radius).toBe("12px");
    expect(layout.inside).toBe(false);
    expect(layout.textBottom).toBeLessThanOrEqual(layout.cardTop);
    expect(layout.anchor).toBe(true);
    await source.hover();
    await p.waitForTimeout(230);
    const hover = await source.evaluate((n) => ({
      background: getComputedStyle(n).backgroundColor,
      copyBackground: getComputedStyle(n.querySelector(".hp-run-reference-copy")).backgroundColor,
      copyShadow: getComputedStyle(n.querySelector(".hp-run-reference-copy")).boxShadow,
      titleColor: getComputedStyle(n.querySelector(".hp-run-reference-copy > span")).color,
      arrowColor: getComputedStyle(n.querySelector("svg")).color,
    }));
    expect(hover.copyBackground).toBe("rgba(0, 0, 0, 0)");
    expect(hover.copyShadow).toBe("none");
    expect(hover.titleColor).toBe("rgb(32, 42, 52)");
    expect(hover.arrowColor).toBe("rgb(185, 64, 37)");
    await p.screenshot({
      path: resolve(out, `frames/card-${scene}-${width}-${edge ? "edge" : "natural"}.png`),
    });
    if (edge)
      await p.evaluate(() => {
        const native = HTMLElement.prototype.getBoundingClientRect;
        HTMLElement.prototype.getBoundingClientRect = function () {
          const r = native.call(this);
          return this.id === "inspector-title"
            ? new DOMRect(r.x, r.y, innerWidth - r.x + 0.0000915527344, r.height)
            : r;
        };
      });
    await source.click();
    await p.waitForTimeout(300);
    await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
    await expect(p.getByRole("tab")).toHaveCount(0);
    await p.screenshot({
      path: resolve(out, `frames/relay-${scene}-${width}-${edge ? "edge" : "natural"}.png`),
    });
    const points = await p.locator(".hp-inspector-relay").evaluate((n) => ({ ...n.dataset })),
      rect = await source.evaluate((n) => n.getBoundingClientRect().toJSON());
    expect(Number(points.sourceX)).toBeCloseTo(rect.right - 4, 1);
    await p.waitForFunction(
      () =>
        document
          .getElementById("inspector-title")
          ?.classList.contains("hp-inspector-title-arrival"),
      null,
      { polling: 10 },
    );
    await p.waitForTimeout(80);
    const titleOnly = await p.evaluate(() => ({
      t: performance.now(),
      title: document.getElementById("inspector-title").textContent,
      color: getComputedStyle(document.getElementById("inspector-title")).color,
      held: document.querySelector("[data-inspector-gate]").hidden,
      tabs: [...document.querySelectorAll('[role="tab"]')].filter(
        (n) => !n.closest("[hidden],[inert]"),
      ).length,
    }));
    expect(titleOnly.title).toBe("执行详情");
    expect(titleOnly.held).toBe(true);
    expect(titleOnly.tabs).toBe(0);
    await p.screenshot({
      path: resolve(out, `frames/title-first-${scene}-${width}-${edge ? "edge" : "natural"}.png`),
    });
    await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
    await expect(p.locator("#inspector-title")).not.toHaveClass(/hp-inspector-title-arrival/);
    if (scene === "slow") await expect(p.getByText("正在加载执行…", { exact: true })).toBeVisible();
    await expect(p.locator(".hp-run-result")).toHaveText("执行 B：第二条结果，独立来源。");
    await p.waitForTimeout(100);
    await expect(p.locator("#inspector-title")).not.toHaveClass(/hp-inspector-title-arrival/);
    await p.screenshot({
      path: resolve(out, `frames/details-after-${scene}-${width}-${edge ? "edge" : "natural"}.png`),
    });
    const frames = await p.evaluate(() => window.__r4log.filter((n) => n.type === "frame")),
      arrival = frames.find((n) => n.titleArrival),
      body = frames.find((n) => n.detailsVisible);
    expect(arrival).toBeTruthy();
    expect(body.t - arrival.t).toBeGreaterThanOrEqual(240);
    expect(frames.filter((n) => n.titleArrival).every((n) => !n.detailsVisible)).toBe(true);
    let groups = 0,
      previous = false;
    for (const frame of frames) {
      if (frame.titleArrival && !previous) groups++;
      previous = !!frame.titleArrival;
    }
    expect(groups).toBe(1);
    const request = await p.evaluate(() =>
      window.__r4log.find((n) => n.type === "request" && n.path.endsWith("/runs/r4-B")),
    );
    expect(request.t).toBeLessThan(arrival.t);
    await p.locator('.hp-inspector button[title="关闭"]').click();
    await p.waitForTimeout(280);
    await p.screenshot({
      path: resolve(out, `frames/return-${width}-${edge ? "edge" : "natural"}.png`),
    });
    await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
    reports.push({
      scene,
      width,
      edge,
      layout,
      hover,
      points,
      titleOnly,
      titleAt: arrival.t,
      detailsAt: body.t,
      titleToDetails: body.t - arrival.t,
      titleGroups: groups,
      request,
      events: await p.evaluate(() => window.__r4events),
    });
    await p.close();
    console.log("sequence", scene, width, edge, "pass");
  }
  const p = await b.newPage({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "no-preference",
  });
  await p.goto("http://127.0.0.1:5323/lab.html");
  await expect(p.locator(".hp-run-detail")).toHaveCount(3);
  await p.locator(".hp-run-detail").first().click();
  await p.waitForFunction(
    () =>
      document.getElementById("inspector-title")?.classList.contains("hp-inspector-title-arrival"),
    null,
    { polling: 10 },
  );
  await p.locator('.hp-inspector button[title="关闭"]').click();
  await p.locator(".hp-run-detail").nth(2).click();
  await p.waitForTimeout(300);
  await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
  await expect(p.locator(".hp-run-result")).toHaveText("执行 C：最后选择的当前结果。");
  await p.close();
  expect(errors).toEqual([]);
  await writeFile(
    resolve(out, "logs/sequence.json"),
    JSON.stringify({ reports, errors, titleInterruptedReopen: "pass" }, null, 2),
  );
} finally {
  await b.close();
}
