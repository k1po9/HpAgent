import console from "node:console";
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-01");
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
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
const state = await login.storageState();
await login.close();
const logs = [];
for (const [version, port] of [
  ["before", 5310],
  ["after", 5294],
])
  for (const width of [1680, 390]) {
    const folder = resolve(out, "clips", `${version}-${width}`);
    await mkdir(folder, { recursive: true });
    const c = await b.newContext({
      storageState: state,
      viewport: { width, height: width === 390 ? 844 : 1000 },
      recordVideo: { dir: folder, size: { width, height: width === 390 ? 844 : 1000 } },
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
      reducedMotion: "no-preference",
    });
    const q = await c.newPage();
    q.setDefaultTimeout(10000);
    const events = [];
    let zero = Date.now();
    const mark = (name) => {
      console.log(version, width, name);
      events.push({ name, ms: Date.now() - zero });
    };
    await q.goto(`http://127.0.0.1:${port}/` + routes.ST02);
    await expect(q.locator(".hp-composer")).toBeVisible();
    await q.waitForTimeout(900);
    mark("ready");
    const nav = q.getByRole("navigation", { name: "主导航" });
    for (const name of ["空间", "任务", "AI"]) {
      mark("Rail " + name);
      await nav.getByRole("button", { name, exact: true }).click();
      await q.waitForTimeout(900);
    }
    if (width === 390) await q.getByRole("button", { name: "打开侧栏", exact: true }).click();
    mark("Conversation re-seat");
    await q.locator(`[data-conversation-id="${routes.ST03.split("/").at(-1)}"]`).click();
    await q.waitForTimeout(900);
    mark("account open");
    await q.getByRole("button", { name: "账户设置", exact: true }).click();
    await q.waitForTimeout(1100);
    await q.screenshot({ path: resolve(folder, "account.png") });
    mark("account close");
    await q.keyboard.press("Escape");
    await q.waitForTimeout(600);
    mark("nested parent open");
    await nav.getByRole("button", { name: "任务", exact: true }).click();
    await q.waitForTimeout(500);
    await q.getByRole("button", { name: "新建任务", exact: true }).click();
    await q.getByLabel("工作名称").fill("仅本地未提交草稿");
    await q.waitForTimeout(500);
    mark("nested child open");
    await q.keyboard.press("Escape");
    await q.waitForTimeout(800);
    mark("return to parent");
    await q.keyboard.press("Escape");
    await expect(q.getByLabel("工作名称")).toHaveValue("仅本地未提交草稿");
    await q.waitForTimeout(800);
    await q.keyboard.press("Escape");
    await q.getByRole("button", { name: "放弃草稿并关闭", exact: true }).click();
    await q.waitForTimeout(700);
    const video = q.video();
    await c.close();
    await video.saveAs(resolve(out, "clips", `${version}-${width}.webm`));
    logs.push({ version, width, events, file: `clips/${version}-${width}.webm` });
  }
await writeFile(
  resolve(out, "logs/clips.json"),
  JSON.stringify(
    {
      browser: await b.version(),
      input: "mouse, native keyboard Enter/Escape; Playwright real UI; no task submission",
      logs,
    },
    null,
    2,
  ),
);
await b.close();
