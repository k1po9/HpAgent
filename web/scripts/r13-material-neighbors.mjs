import process from "node:process";
import "./review-runtime.mjs";
/* global document, innerWidth */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./r13-material-contrast.mjs";
import console from "node:console";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-04"),
  dir = resolve(out, "screenshots/neighbors-final");
await mkdir(dir, { recursive: true });
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" });
const metrics = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  for (const width of [1680, 390])
    for (const scene of ["workspace", "file", "task", "tasks"])
      for (const variant of ["before", "after"]) {
        await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        await p.goto(
          (variant === "before" ? "http://127.0.0.1:5301" : "http://127.0.0.1:5294") +
            "/" +
            routes[scene],
        );
        await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
        if (scene === "workspace" || scene === "file") {
          await expect(p.locator(".hp-file-table")).toBeVisible();
          await expect(p.locator(".hp-workspace-sidebar")).not.toContainText("正在加载");
          if (scene === "file") await expect(p.locator(".hp-file-preview")).toBeVisible();
        } else {
          await expect(p.locator(".hp-task-row")).toHaveCount(1);
          await expect(p.locator(".hp-task-screen > [role=status]")).toContainText("本轮同步完成");
          if (scene === "task") await expect(p.locator(".hp-task-inspector")).toBeVisible();
        }
        await p.evaluate(() => document.activeElement?.blur());
        const doc = await p.evaluate(() => ({
          width: innerWidth,
          scroll: document.documentElement.scrollWidth,
        }));
        expect(doc.scroll).toBeLessThanOrEqual(doc.width + 1);
        const name = `${scene}-${width}-${variant}`;
        await p.screenshot({ path: resolve(dir, name + ".png") });
        metrics.push({
          name,
          doc,
          contrast: await auditContrast(p),
          text: await p.locator(".hp-main-canvas").innerText(),
        });
      }
  console.log(metrics.length, "neighbor images", errors.length, "page errors");
} finally {
  await writeFile(
    resolve(out, "logs/neighbors-final.json"),
    JSON.stringify({ metrics, errors }, null, 2),
  );
  await b.close();
}
