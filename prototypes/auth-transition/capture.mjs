import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";
const browser = await chromium.launch();
const rows = [];
for (const mode of ["particles", "flip"]) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1100 },
    recordVideo: {
      dir: "test-results/videos",
      size: { width: 1440, height: 1100 },
    },
  });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:5310");
  await page
    .getByRole("button", {
      name: mode === "particles" ? /粒子解构/ : /空间变形/,
    })
    .click();
  await page
    .getByRole("combobox", { name: "粒子预算" })
    .selectOption("standard");
  await page.waitForTimeout(350);
  await page.screenshot({ path: `evidence/${mode}-before.png` });
  await page.getByRole("button", { name: "模拟登录成功" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `evidence/${mode}-during.png` });
  await page.locator(".scene[data-state=settled]").waitFor();
  rows.push({
    mode,
    condition: "desktop + recording",
    metrics: await page.locator("tbody tr").first().innerText(),
  });
  await page.screenshot({ path: `evidence/${mode}-after.png` });
  await page.waitForTimeout(650);
  const video = page.video();
  await context.close();
  await video.saveAs(`evidence/${mode}.webm`);
}
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
});
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
await page.goto("http://127.0.0.1:5310");
await page.getByRole("combobox", { name: "粒子预算" }).selectOption("low");
for (const mode of ["particles", "flip"]) {
  await page
    .getByRole("button", {
      name: mode === "particles" ? /粒子解构/ : /空间变形/,
    })
    .click();
  await page.getByRole("button", { name: "模拟登录成功" }).click();
  await page.locator(".scene[data-state=settled]").waitFor();
  rows.push({
    mode,
    condition: "4x CPU / low budget",
    metrics: await page.locator("tbody tr").first().innerText(),
  });
}
await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({ path: "evidence/mobile-after.png", fullPage: true });
await browser.close();
await writeFile("evidence/metrics.json", JSON.stringify(rows, null, 2) + "\n");
console.log(rows);
