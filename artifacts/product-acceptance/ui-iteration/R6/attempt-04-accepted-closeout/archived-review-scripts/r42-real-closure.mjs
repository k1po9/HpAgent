/* global URL, console, window, document, location, performance, requestAnimationFrame, process, Buffer */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile, readFile, mkdir } from "node:fs/promises";
import { randomUUID } from "node:crypto";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const only = process.env.REAL_ONLY;
const results = only
    ? JSON.parse(await readFile(out + "/evidence/real-closure.json", "utf8")).results.filter(
        (r) => r.name !== only,
      )
    : [],
  errors = [];
const base = "http://127.0.0.1:5334";
const close = (p) => p.locator('.hp-inspector button[title="关闭"]');
async function ready(p, kind) {
  await expect(p.locator(".hp-inspector--" + kind)).toBeVisible();
  await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
}
async function check(name, user, fn) {
  if (only && name !== only) return;
  const p = await browser.newPage({
      viewport: { width: 1680, height: 1000 },
      reducedMotion: "reduce",
    }),
    network = [];
  p.setDefaultTimeout(15000);
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("request", (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith("/api/") || u.pathname.startsWith("/auth/"))
      network.push({
        event: "request",
        t: Date.now(),
        path: u.pathname,
        method: r.method(),
        idempotency: r.headers()["idempotency-key"],
      });
  });
  p.on("response", (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith("/api/") || u.pathname.startsWith("/auth/"))
      network.push({ event: "response", t: Date.now(), path: u.pathname, status: r.status() });
  });
  await p.addInitScript(() => {
    window.realTimeline = [];
    window.realEvents = [];
    window.addEventListener("hp-inspector-relay-event", (e) =>
      window.realEvents.push({ t: performance.now(), ...e.detail }),
    );
    window.realSampling = true;
    function tick() {
      const relay = document.querySelector(".hp-action-relay"),
        gate = document.querySelector("[data-inspector-gate]"),
        silk = document.querySelector(".hp-inspector-relay");
      window.realTimeline.push({
        t: performance.now(),
        epoch: Date.now(),
        hash: location.hash,
        action: relay
          ? {
              phase: relay.dataset.phase,
              run: relay.dataset.runId,
              head: relay.dataset.head,
              travel: relay.dataset.travelLength,
            }
          : null,
        phase: document.querySelector("[data-inspector-phase]")?.dataset.inspectorPhase,
        hidden: gate?.hidden,
        title: !!document.querySelector(".hp-inspector-title-arrival"),
        inspectorTip: silk
          ? { tip: silk.dataset.tipLength, length: silk.dataset.totalLength }
          : null,
      });
      if (window.realSampling) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  });
  try {
    await p.goto(base);
    await p.getByLabel("用户名").fill(user);
    await p.getByLabel("密码", { exact: true }).fill("r416-review-password");
    await p.getByRole("button", { name: "登录", exact: true }).click();
    await expect(p.locator(".hp-shell")).toBeVisible();
    await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
    await p.emulateMedia({ reducedMotion: "no-preference" });
    const me = await (await p.request.get(base + "/api/v1/me")).json();
    const headers = { Origin: base, "X-CSRF-Token": me.csrf_token };
    const api = async (method, path, data, status = 200) => {
      const response = await p.request.fetch(base + path, {
        method,
        headers: { ...headers, "Idempotency-Key": randomUUID() },
        data,
      });
      expect(response.status(), await response.text()).toBe(status);
      return response;
    };
    const evidence = await fn(p, api, headers);
    results.push({ name, result: "pass", evidence });
    console.log("PASS", name);
  } catch (e) {
    results.push({ name, result: "fail", error: String(e) });
    console.log("FAIL", name, String(e));
  }
  await p.screenshot({ path: out + "/screenshots/real-" + name + ".png" }).catch(() => {});
  await p.evaluate(() => (window.realSampling = false)).catch(() => {});
  await writeFile(
    out + "/evidence/real-" + name + ".json",
    JSON.stringify(
      {
        network,
        browser: browser.version(),
        ...(await p
          .evaluate(() => ({
            hash: location.hash,
            events: window.realEvents,
            timeline: window.realTimeline,
            focus: document.activeElement?.outerHTML.slice(0, 400),
          }))
          .catch(() => ({}))),
      },
      null,
      2,
    ),
  );
  await p.close();
  await writeFile(
    out + "/evidence/real-closure.json",
    JSON.stringify(
      {
        results,
        errors,
        realAPI: true,
        executors:
          "repository FakeRunExecutor and FakeArtifactExecutor; isolated actual PostgreSQL, Redis, roles and files; no real supplier or QQ",
      },
      null,
      2,
    ),
  );
}
async function conversation(p, api) {
  const response = await api("POST", "/api/v1/conversations", { title: "R4 隔离综合验收" }, 201);
  const id = (await response.json()).conversation.conversation_id;
  await p.goto(base + "/#/ai/" + id);
  await expect(p.getByLabel("消息输入")).toBeVisible();
  return id;
}
async function send(p, text) {
  await p.getByLabel("消息输入").fill(text);
  const accepted = p.waitForResponse(
    (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/messages"),
  );
  await p.getByRole("button", { name: "发送", exact: true }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  return response.json();
}
await mkdir(out + "/screenshots", { recursive: true });
await check("complete-chain", "r416_closure", async (p, api) => {
  const conv = await conversation(p, api),
    tree = await (await p.request.get(base + "/api/v1/workspace")).json(),
    parent = tree.nodes.find((n) => n.name === "资料").node_id;
  const unique = Date.now();
  const content = Buffer.from("# R4 isolated source\nActual file, authorization and download.\n");
  const uploaded = await (
    await api(
      "POST",
      "/api/v1/workspace/uploads",
      {
        file_name: "r416-source-" + unique + ".md",
        content_type: "text/markdown",
        size_bytes: content.length,
      },
      201,
    )
  ).json();
  await api("PUT", uploaded.content_url, content, 200);
  const node = await (
    await api(
      "POST",
      "/api/v1/workspace/files",
      { parent_id: parent, file_id: uploaded.file.file_id, name: "r416-source-" + unique + ".md" },
      201,
    )
  ).json();
  await api(
    "POST",
    `/api/v1/conversations/${conv}/resources`,
    { node_id: parent, operations: ["list_metadata", "read_content"], recursive: true },
    201,
  );
  const accepted = await send(p, "R4 整链：发送、执行、查看资料与成果，关闭后继续输入。");
  await expect(p.locator(".hp-action-relay")).toHaveCount(1);
  await p.screenshot({ path: out + "/screenshots/real-send-travelling.png" });
  await expect(p.locator(".hp-action-relay")).toHaveCount(0);
  await p.getByLabel("消息输入").fill("四类阅读期间保留的输入草稿");
  await p.getByLabel("消息输入").evaluate((n) => (window.originalInput = n));
  const source = p.locator("#run-open-" + accepted.run.run_id);
  await source.click();
  await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
  await ready(p, "run");
  await p.screenshot({ path: out + "/screenshots/real-run-landed.png" });
  await p.getByRole("tab", { name: "使用资料与输出", exact: true }).click();
  await p.locator(`#run-file-${accepted.run.run_id}-${node.node_id}`).click();
  await ready(p, "file");
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(p.locator(".hp-file-inspector")).toContainText("Actual file");
  const download = p.waitForEvent("download");
  await p.locator(".hp-file-inspector").getByRole("link", { name: "下载", exact: true }).click();
  const d = await download;
  const path = await d.path();
  expect(await readFile(path)).toEqual(content);
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await ready(p, "run");
  await expect(p).toHaveURL(/tab=resources/);
  await close(p).click();
  await expect(p.locator(".hp-inspector")).toHaveCount(0);
  await expect(p.locator('[data-testid="run-label"]')).toHaveText(/已完成/, { timeout: 45000 });
  await p.getByRole("button", { name: "生成 HTML", exact: true }).click();
  await ready(p, "artifact");
  await expect(p.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30000 });
  const firstVersion = new URL(p.url()).hash;
  const frame = p.getByTitle("Artifact 预览");
  await frame.evaluate((n) => (window.originalFrame = n));
  await p
    .frameLocator('iframe[title="Artifact 预览"]')
    .getByRole("button", { name: "切换状态" })
    .click();
  for (const tab of ["版本历史", "详情", "预览"])
    await p.getByRole("tab", { name: tab, exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  await p.locator(".hp-inspector-expand").click();
  expect(await frame.evaluate((n) => n === window.originalFrame)).toBe(true);
  await expect(
    p.frameLocator('iframe[title="Artifact 预览"]').getByText("开启", { exact: true }),
  ).toBeVisible();
  await p.getByRole("button", { name: "保存源码副本到空间", exact: true }).click();
  const dialog = p.getByRole("dialog", { name: "保存到长期文件" });
  await dialog.getByLabel("保存名称").fill("r416-generated.html.txt");
  const saved = p.waitForResponse(
    (r) =>
      r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v1/workspace/files",
  );
  await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
  expect((await saved).status()).toBe(201);
  await expect(dialog).toHaveCount(0);
  await p.screenshot({ path: out + "/screenshots/real-artifact-saved.png" });
  await p.getByRole("button", { name: "打开空间", exact: true }).click();
  await ready(p, "file");
  await expect(p.locator(".hp-file-inspector")).toContainText("r416-generated.html.txt");
  await close(p).click();
  await p
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "任务", exact: true })
    .click();
  await p.getByRole("button", { name: "新建任务", exact: true }).click();
  await p.getByLabel("工作名称").fill("R416 计划任务");
  await p.getByLabel("目标 / 提醒内容").fill("隔离计划要求");
  await p.getByLabel("执行时间").selectOption("once");
  await p.getByLabel("日期时间").fill("2027-01-01T09:00");
  const created = p.waitForResponse(
    (r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/v1/works",
  );
  await p.getByRole("button", { name: "创建工作", exact: true }).click();
  const task = (await (await created).json()).work;
  await ready(p, "task");
  await p.locator(".hp-inspector").getByRole("button", { name: "修改要求", exact: true }).click();
  await p.getByLabel("目标 / 提醒内容").fill("隔离已修订要求");
  await p.getByLabel("修订原因").fill("R4 真实API核实");
  await p.getByRole("button", { name: "提交修订", exact: true }).click();
  await expect(p.getByRole("dialog", { name: "修改任务要求" })).toHaveCount(0);
  await expect(p.locator(".hp-task-inspector")).toContainText("隔离已修订要求");
  await p.getByRole("tab", { name: "使用资料", exact: true }).click();
  await p.getByLabel("选择长期目录或文件").selectOption(parent);
  await p.getByLabel("包含此目录的所有子目录与文件").check();
  await p.getByRole("button", { name: "确认授予所选权限", exact: true }).click();
  await expect(p.getByRole("status").filter({ hasText: "授权已生效" })).toBeVisible();
  const grants = (
    await (await p.request.get(base + `/api/v1/works/${task.work_id}/resources`)).json()
  ).grants;
  expect(grants).toHaveLength(2);
  await p.getByRole("button", { name: "撤销规则", exact: true }).first().click();
  await p
    .getByRole("dialog", { name: "确认撤销资料授权" })
    .getByRole("button", { name: "确认撤销", exact: true })
    .click();
  await expect(p.getByRole("dialog", { name: "确认撤销资料授权" })).toHaveCount(0);
  const afterRevoke = (
    await (await p.request.get(base + `/api/v1/works/${task.work_id}/resources`)).json()
  ).grants;
  expect(afterRevoke).toHaveLength(1);
  await p.screenshot({ path: out + "/screenshots/real-task-revised-grants.png" });
  await close(p).click();
  await p
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "AI", exact: true })
    .click();
  await expect(p.getByLabel("消息输入")).toHaveValue("四类阅读期间保留的输入草稿");
  await send(p, "R4 关闭全部详情后继续发送。");
  await expect(p.locator('[data-testid="run-label"]')).toHaveText(/已完成/, { timeout: 45000 });
  return {
    conv,
    run: accepted.run.run_id,
    file: uploaded.file.file_id,
    node: node.node_id,
    firstVersion,
    task: task.work_id,
    taskExecution: "not-run; future scheduled task; FakeRunExecutor excludes Work runs",
    grantsBefore: grants.length,
    grantsAfter: afterRevoke.length,
    downloadBytes: content.length,
  };
});
for (const phase of ["travelling", "forming"])
  await check(
    "r3-interrupt-" + phase,
    phase === "travelling" ? "r416_travel" : "r416_form",
    async (p, api) => {
      const conv = await conversation(p, api);
      const accepted = await send(p, "R3 " + phase + " 时打开 Inspector。");
      await expect(p.locator(".hp-action-relay")).toHaveAttribute("data-phase", phase);
      await p.screenshot({ path: out + "/screenshots/real-r3-" + phase + ".png" });
      await p.locator("#run-open-" + accepted.run.run_id).click();
      await expect(p.locator(".hp-action-relay")).toHaveCount(0);
      await ready(p, "run");
      await close(p).click();
      await expect(p.locator(".hp-action-relay")).toHaveCount(0);
      await expect(p.locator('[data-testid="run-label"]')).toHaveText(/已完成/, { timeout: 45000 });
      const messages = (
        await (await p.request.get(base + `/api/v1/conversations/${conv}/messages`)).json()
      ).items;
      expect(messages.filter((m) => m.role === "user")).toHaveLength(1);
      return { phase, run: accepted.run.run_id, noResend: true };
    },
  );
await browser.close();
if (results.some((r) => r.result === "fail") || errors.length) process.exitCode = 1;
