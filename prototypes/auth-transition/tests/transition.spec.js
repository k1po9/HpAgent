import { test, expect } from "@playwright/test";

for (const mode of ["粒子解构", "空间变形"]) {
  test(`${mode}: duration, continuity, focus and particle teardown`, async ({
    page,
  }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await page.getByRole("button", { name: new RegExp(mode) }).click();
    await page
      .locator(".workspace")
      .evaluate((el) => (el.dataset.identity = "preserved"));
    await page.getByRole("button", { name: "模拟登录成功" }).click();
    await expect(page.locator(".scene")).toHaveAttribute(
      "data-state",
      "running",
    );
    await expect(page.locator(".scene")).toHaveAttribute(
      "data-state",
      "settled",
    );
    await expect(page.locator(".workspace")).toHaveAttribute(
      "data-identity",
      "preserved",
    );
    await expect(
      page.getByRole("textbox", { name: "工作台输入框" }),
    ).toBeFocused();
    await expect(page.locator("canvas")).toHaveCount(0);
    const timing = await page
      .locator("tbody tr")
      .first()
      .locator("td")
      .nth(1)
      .innerText();
    const actual = +timing.split("/")[1].replace("ms", "").trim();
    expect(actual).toBeGreaterThanOrEqual(900);
    expect(actual).toBeLessThanOrEqual(1600);
    const before = await page.locator(".auth-surface").boundingBox();
    await page.waitForTimeout(150);
    expect(await page.locator(".auth-surface").boundingBox()).toEqual(before);
    expect(errors).toEqual([]);
  });
}

test("OS reduced motion: no canvas, quick completion for both modes", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  for (const mode of ["粒子解构", "空间变形"]) {
    await page.getByRole("button", { name: new RegExp(mode) }).click();
    await page.getByRole("button", { name: "模拟登录成功" }).click();
    await expect(page.locator("canvas")).toHaveCount(0);
    await expect(page.locator(".scene")).toHaveAttribute(
      "data-state",
      "settled",
    );
    await expect(page.locator("tbody tr").first()).toContainText(
      "无粒子快速切换",
    );
    const text = await page
      .locator("tbody tr")
      .first()
      .locator("td")
      .nth(1)
      .innerText();
    expect(+text.split("/")[1].replace("ms", "").trim()).toBeLessThan(450);
  }
});

test("cancel/replay and viewport changes do not strand the UI", async ({
  page,
}) => {
  await page.goto("/");
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "模拟登录成功" }).click();
    await page.getByRole("button", { name: "取消 / 重置" }).click();
    await expect(page.locator(".scene")).toHaveAttribute("data-state", "ready");
    await expect(page.locator("canvas")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "模拟登录成功" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".scene")).toHaveAttribute("data-state", "settled");
  await expect(
    page.getByRole("textbox", { name: "工作台输入框" }),
  ).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("low budget under 4x CPU throttle, screenshot evidence", async ({
  page,
}, testInfo) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.goto("/");
  await page.getByRole("combobox", { name: "粒子预算" }).selectOption("low");
  await page.screenshot({
    path: testInfo.outputPath("01-auth.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "模拟登录成功" }).click();
  await expect(page.locator(".scene")).toHaveAttribute("data-state", "settled");
  await expect(page.locator("tbody tr").first()).toContainText("48 / 低功耗");
  await page.screenshot({
    path: testInfo.outputPath("02-workspace.png"),
    fullPage: true,
  });
  await testInfo.attach("4x-cpu-metrics", {
    body: await page.locator("tbody tr").first().innerText(),
    contentType: "text/plain",
  });
});

test("duration presets and manual reduced-motion toggle", async ({ page }) => {
  await page.goto("/");
  for (const duration of ["900", "1600"]) {
    await page
      .getByRole("combobox", { name: "转场时长" })
      .selectOption(duration);
    await page.getByRole("button", { name: "模拟登录成功" }).click();
    await expect(page.locator(".scene")).toHaveAttribute(
      "data-state",
      "settled",
    );
    const text = await page
      .locator("tbody tr")
      .first()
      .locator("td")
      .nth(1)
      .innerText();
    expect(
      Math.abs(+text.split("/")[1].replace("ms", "").trim() - +duration),
    ).toBeLessThan(130);
  }
  await page.getByLabel("减少动态效果", { exact: true }).check();
  await page.getByRole("button", { name: "模拟登录成功" }).click();
  await expect(page.locator("canvas")).toHaveCount(0);
  await expect(page.locator(".scene")).toHaveAttribute("data-state", "settled");
  await expect(page.locator("tbody tr").first()).toContainText(
    "无粒子快速切换",
  );
});

test("mid-flight OS preference change settles and releases promoted layers", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: "模拟登录成功" }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".scene")).toHaveAttribute("data-state", "settled");
  await expect(page.locator("canvas")).toHaveCount(0);
  await expect(page.locator(".auth-surface")).toHaveCSS("will-change", "auto");
  await expect(
    page.getByRole("textbox", { name: "工作台输入框" }),
  ).toBeFocused();
  expect(errors).toEqual([]);
});

test("particle painter erases its previous bounds and tolerates repeated disposal", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { makeParticles, createParticlePainter } =
      await import("/src/particles.js");
    const canvas = document.createElement("canvas");
    const particles = makeParticles(
      { x: 50, y: 20, width: 80, height: 90 },
      [
        { x: 0, y: 0, width: 30, height: 160 },
        { x: 80, y: 110, width: 100, height: 35 },
      ],
      48,
    );
    const painter = createParticlePainter(canvas, particles, 200, 160, 1);
    const countPixels = () =>
      canvas
        .getContext("2d")
        .getImageData(0, 0, 200, 160)
        .data.filter((value, index) => index % 4 === 3 && value > 0).length;
    painter.draw(0.3);
    const during = countPixels();
    painter.draw(0.6);
    painter.draw(1);
    const after = countPixels();
    painter.dispose();
    painter.dispose();
    painter.draw(0.3);
    return { during, after, width: canvas.width, height: canvas.height };
  });
  expect(result.during).toBeGreaterThan(0);
  expect(result.after).toBe(0);
  expect(result.width).toBe(0);
  expect(result.height).toBe(0);
});

for (const scenario of ["success", "slow", "expired", "error"]) {
  test(`Cookie recovery: ${scenario}`, async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Cookie 恢复/ }).click();
    await page.getByLabel("恢复场景").selectOption(scenario);
    await page.getByRole("button", { name: "模拟 Cookie 恢复" }).click();
    await expect(page.locator(".recovery-ring")).toHaveClass(/spinning/);
    if (scenario === "expired") {
      await expect(
        page.getByRole("button", { name: "模拟登录成功" }),
      ).toBeVisible();
      await expect(page.locator("canvas")).toHaveCount(0);
    } else if (scenario === "error") {
      await expect(page.getByRole("alert")).toHaveText("连接失败，请重试");
      await expect(
        page.getByRole("button", { name: "重试恢复" }),
      ).toBeVisible();
      await expect(page.locator("canvas")).toHaveCount(0);
    } else {
      await expect(page.locator(".scene")).toHaveAttribute(
        "data-state",
        "settled",
        { timeout: 7000 },
      );
      await expect(page.locator("canvas")).toHaveCount(0);
    }
  });
}
