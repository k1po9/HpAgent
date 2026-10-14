import "./r1-isolation.mjs";
import { URL } from "node:url";
import console from "node:console";
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1/attempt-01");
const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const page = await browser.newPage({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
});
const base = "http://127.0.0.1:5294";
try {
  await page.goto(base + "/?r1=s1");

  await page.getByLabel("用户名").fill("r1_review");
  await page.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");

  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".hp-shell")).toBeVisible();
  const skip = page.getByRole("button", { name: /以后再说/ });
  if (await skip.count()) await skip.first().click();
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "false");
  const routes = {};
  for (const [key, text] of [
    ["ST02", "请整理项目评审重点，并准备一份可打开的 HTML 成果。"],
    ["ST03", "请提供一份含中英文、引用、表格和代码的完整评审说明。"],
    ["ST04", "请继续整理多个交付对象。"],
  ]) {
    await page.getByRole("button", { name: "新建对话", exact: true }).click();
    await page.getByLabel("消息输入").fill(text);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByTestId("run-label")).toHaveText("已完成");
    routes[key] = new URL(page.url()).hash;
    if (key === "ST02") {
      await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
      await expect(page.getByTitle("Artifact 预览")).toBeVisible();
      await expect(page.frameLocator("iframe").getByRole("heading")).toBeVisible();
      routes.artifact = new URL(page.url()).hash;
      await page.getByRole("button", { name: "关闭HTML 成果" }).click();
      await page.getByRole("button", { name: "查看执行详情", exact: true }).click();
      routes.ST07 = new URL(page.url()).hash;
      await page.getByRole("button", { name: "关闭执行详情" }).click();
    }
  }
  await writeFile(resolve(out, "routes.json"), JSON.stringify(routes, null, 2));
  console.log("Seeded 3 conversations and an Artifact via real UI + isolated API.");
} finally {
  await browser.close();
}
