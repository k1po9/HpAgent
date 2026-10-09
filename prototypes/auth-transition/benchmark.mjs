import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
const label = process.argv[2] || "current";
const browser = await chromium.launch();
const results = [];
try {
  for (const [rate, budget] of [
    [1, "standard"],
    [4, "standard"],
    [4, "low"],
  ]) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1100 },
    });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate });
    await page.goto("http://localhost:5310");
    await page.getByRole("combobox", { name: "粒子预算" }).selectOption(budget);
    const runs = [];
    for (let i = 0; i < 6; i++) {
      await page.getByRole("button", { name: "模拟登录成功" }).click();
      await page.locator(".scene[data-state=settled]").waitFor();
      const cells = await page
        .locator("tbody tr")
        .first()
        .locator("td")
        .allTextContents();
      const [slow, samples] = cells[3].split("/").map(Number);
      if (i)
        runs.push({
          setupMs: Number(
            await page
              .locator("tbody tr")
              .first()
              .getAttribute("data-setup-ms"),
          ),
          elapsed: Number(cells[1].split("/")[1].replace("ms", "")),
          p95: parseFloat(cells[2]),
          slow,
          samples,
          budget: cells[4],
        });
      await page.getByRole("button", { name: /重置演示/ }).click();
    }
    const median = (values) =>
      [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
    results.push({
      rate,
      budget,
      medianElapsed: median(runs.map((r) => r.elapsed)),
      medianSetup: median(runs.map((r) => r.setupMs)),
      medianP95: median(runs.map((r) => r.p95)),
      slowFrameRatio: +(
        runs.reduce((s, r) => s + r.slow, 0) /
        runs.reduce((s, r) => s + r.samples, 0)
      ).toFixed(3),
      runs,
    });
    await context.close();
  }
} finally {
  await browser.close();
}
await mkdir("evidence", { recursive: true });
await writeFile(
  `evidence/benchmark-${label}.json`,
  JSON.stringify(
    {
      label,
      conditions:
        "Chromium headless, 1440×1100, no recording/screenshots; one warmup + five measured runs per condition; shared host",
      results,
    },
    null,
    2,
  ) + "\n",
);
console.log(results.map(({ runs, ...summary }) => summary));
