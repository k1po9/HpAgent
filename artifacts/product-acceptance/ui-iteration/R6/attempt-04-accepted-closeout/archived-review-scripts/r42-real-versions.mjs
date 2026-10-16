/* global window, console, process, URL, URLSearchParams */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure");
const browser = await chromium.launch({ args: ["--no-sandbox"] }),
  p = await browser.newPage({
    baseURL: "http://127.0.0.1:5336",
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "reduce",
  });
p.setDefaultTimeout(15000);
const errors = [],
  requests = [];
p.on("pageerror", (e) => errors.push(e.message));
p.on("response", (r) => {
  if (new URL(r.url()).pathname.startsWith("/api"))
    requests.push({
      path: new URL(r.url()).pathname,
      method: r.request().method(),
      status: r.status(),
    });
});
let report;
try {
  await p.goto("/");
  await p.getByLabel("用户名").fill("alice");
  await p.getByLabel("密码", { exact: true }).fill("e2e-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-shell")).toBeVisible();
  const conversations = (await (await p.request.get("/api/v1/conversations")).json()).items;
  const conv = conversations[0].conversation_id;
  const messages = (
    await (await p.request.get("/api/v1/conversations/" + conv + "/messages")).json()
  ).items;
  const assistant = messages.find((m) => m.role === "assistant");
  const artifacts = (
    await (await p.request.get("/api/v1/messages/" + assistant.message_id + "/artifacts")).json()
  ).items;
  const artifactEntry = artifacts[0];
  const chain = {
    conv,
    firstVersion:
      "#/ai/" +
      conv +
      "?inspect=artifact%3A" +
      artifactEntry.artifact.artifact_id +
      "&version=" +
      artifactEntry.latest_version.artifact_version_id,
  };
  await p.goto("/" + chain.firstVersion);
  await expect(p.getByTitle("Artifact 预览")).toBeVisible();
  const artifact = new URLSearchParams(chain.firstVersion.split("?")[1]).get("inspect").slice(9),
    originalVersion = new URLSearchParams(chain.firstVersion.split("?")[1]).get("version");
  await p.evaluate(() => {
    window.versionEvents = [];
    window.addEventListener("hp-inspector-relay-event", (e) => window.versionEvents.push(e.detail));
  });
  await expect(p.locator("[data-inspector-gate]")).toHaveAttribute(
    "data-inspector-gate",
    "settled",
  );
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p
    .getByLabel("修改指令")
    .fill("R416 real API: preserve earlier version and add a manual successor");
  await expect(p.getByLabel("修改指令")).toHaveValue(
    "R416 real API: preserve earlier version and add a manual successor",
  );
  const creating = p.waitForResponse(
    (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/versions"),
  );
  const [created] = await Promise.all([
    creating,
    p.getByRole("button", { name: "生成新版本", exact: true }).click(),
  ]);
  expect(created.status()).toBe(202);
  const version = (await created.json()).version;
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await p
    .getByRole("button", { name: "查看 v" + version.version, exact: true })
    .first()
    .click();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(p.locator(".hp-artifact-heading")).toHaveAttribute("data-status", "completed", {
    timeout: 30000,
  });
  await expect(p.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30000 });
  await expect(p.locator(".hp-artifact-heading")).toContainText("v" + version.version);
  await p.getByTitle("Artifact 预览").evaluate((n) => (window.versionFrame = n));
  for (const tab of ["详情", "版本历史", "预览"])
    await p.getByRole("tab", { name: tab, exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  await p.locator(".hp-inspector-expand").click();
  expect(await p.getByTitle("Artifact 预览").evaluate((n) => n === window.versionFrame)).toBe(true);
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await p
    .getByRole("button", { name: "查看 v" + artifactEntry.latest_version.version, exact: true })
    .first()
    .click();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(p).toHaveURL(new RegExp("version=" + originalVersion));
  await expect(p.getByTitle("Artifact 预览")).toBeVisible();
  expect(await p.getByTitle("Artifact 预览").evaluate((n) => n === window.versionFrame)).toBe(
    false,
  );
  expect(await p.evaluate(() => window.versionEvents.length)).toBe(0);
  await p.screenshot({ path: out + "/screenshots/real-version-switch.png" });
  await p.locator('.hp-inspector button[title="关闭"]').click();
  await p.getByLabel("消息输入").fill("R416 stop during accepted handoff");
  const sending = p.waitForResponse(
    (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/messages"),
  );
  await p.getByRole("button", { name: "发送", exact: true }).click();
  const accepted = await (await sending).json();
  const cancelled = p.waitForResponse(
    (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/cancel"),
  );
  await p.getByRole("button", { name: "停止", exact: true }).click();
  expect((await cancelled).status()).toBe(202);
  await expect(p.locator('[data-testid="run-label"]').last()).toHaveText(/已停止/, {
    timeout: 30000,
  });
  await expect(p.locator(".hp-action-relay")).toHaveCount(0);
  const snapshot = await (await p.request.get("/api/v1/runs/" + accepted.run.run_id)).json();
  expect(snapshot.run.status).toBe("cancelled");
  report = {
    result: "pass",
    artifact,
    originalVersion,
    newVersion: version.artifact_version_id,
    iframeAcrossTabs: true,
    iframeReplacedOnlyForVersion: true,
    noRelayForInternalVersion: true,
    stoppedRun: accepted.run.run_id,
    runStatus: snapshot.run.status,
  };
} catch (e) {
  report = { result: "fail", error: String(e) };
}
await p.screenshot({ path: out + "/screenshots/real-version-final.png" }).catch(() => {});
await writeFile(
  out + "/evidence/real-versions.json",
  JSON.stringify(
    {
      report,
      requests,
      errors,
      realAPI: true,
      executor: "repository FakeArtifactExecutor/FakeRunExecutor",
    },
    null,
    2,
  ),
);
console.log(JSON.stringify(report, null, 2));
await browser.close();
if (report.result === "fail" || errors.length) process.exitCode = 1;
