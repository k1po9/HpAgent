import "./review-runtime.mjs";
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import process from "node:process";
import { URL } from "node:url";
import console from "node:console";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-01"),
  phase = process.argv[2];
const stage = process.argv[3] && process.argv[3] !== "--isolated" ? process.argv[3] : "real-states";
const dir = resolve(out, "screenshots", stage);
await mkdir(dir, { recursive: true });
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
const proof = [];
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  let route;
  if (phase === "queued" || phase === "failure") {
    await p.goto("http://127.0.0.1:5294/#/ai");
    await expect(p.getByLabel("消息输入")).toBeVisible();
    await p.getByLabel("消息输入").fill("R1.3真实生命周期静态检查");
    const response = p.waitForResponse(
      (r) => r.request().method() === "POST" && /\/messages$/.test(new URL(r.url()).pathname),
    );
    await p.getByRole("button", { name: "发送", exact: true }).click();
    const dto = await (await response).json();
    console.log("command response keys", Object.keys(dto));
    await expect(p.getByTestId("run-label")).toHaveText(phase === "queued" ? "排队中…" : "运行中…");
    route = new URL(p.url()).hash;
    await writeFile(resolve(out, "logs/real-state-route.json"), JSON.stringify({ route }, null, 2));
    await p.screenshot({
      path: resolve(dir, phase === "queued" ? "queued-1680.png" : "running-1680.png"),
    });
  } else {
    route = JSON.parse(await readFile(resolve(out, "logs/real-state-route.json"))).route;
    await p.goto("http://127.0.0.1:5294/" + route);
    await expect(p.getByTestId("run-label")).toHaveText("运行中…");
    await p.screenshot({ path: resolve(dir, "running-1680.png") });
  }
  const conversationId = route.split("/").at(-1),
    base = "http://127.0.0.1:5294";
  const snapshot = await (
    await p.request.get(base + "/api/v1/conversations/" + conversationId)
  ).json();
  const run = snapshot.active_run.run;
  expect(run.status).toBe(phase === "queued" ? "queued" : "running");
  proof.push({
    phase: "authoritative " + run.status,
    run_id: run.run_id,
    conversation_id: conversationId,
    source: "real API GET, no intercepted DTO",
  });
  if (phase !== "queued") {
    await expect(p.getByTestId("run-label")).toHaveText("运行失败", { timeout: 20000 });
    const final = await (await p.request.get(base + "/api/v1/runs/" + run.run_id)).json();
    expect(final.run.status).toBe("failed");
    proof.push({
      phase: "authoritative failed",
      run_id: run.run_id,
      failure: final.run.failure,
      source: "real lifecycle/Fake executor + API GET",
    });
    await p.screenshot({ path: resolve(dir, "failed-1680.png") });
    await p.setViewportSize({ width: 390, height: 844 });
    await p.screenshot({ path: resolve(dir, "failed-390.png") });
    await p.getByRole("button", { name: "查看执行详情", exact: true }).click();
    await expect(p.locator(".hp-inspector-card")).toBeVisible();
    await p.screenshot({ path: resolve(dir, "failed-inspector-390.png") });
  }
} finally {
  await writeFile(
    resolve(out, "logs/" + stage + "-" + phase + ".json"),
    JSON.stringify(proof, null, 2),
  );
  await b.close();
}
