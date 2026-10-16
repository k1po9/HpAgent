/* global URL, URLSearchParams, console, process, window,document,performance,location,requestAnimationFrame */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFile, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure"),
  chain = JSON.parse(await readFile(out + "/evidence/real-closure.json", "utf8")).results.find(
    (r) => r.name === "complete-chain",
  ).evidence;
const artifact = new URLSearchParams(chain.firstVersion.split("?")[1]).get("inspect").slice(9);
const browser = await chromium.launch({ args: ["--no-sandbox"] }),
  reports = [],
  errors = [];
async function login(user) {
  const p = await browser.newPage({
    baseURL: "http://127.0.0.1:5334",
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "reduce",
  });
  p.setDefaultTimeout(12000);
  p.on("pageerror", (e) => errors.push(e.message));
  await p.addInitScript(() => {
    window.apiEvents = [];
    window.apiFrames = [];
    window.addEventListener("hp-inspector-relay-event", (e) =>
      window.apiEvents.push({ t: performance.now(), ...e.detail }),
    );
    window.startSampling = () => {
      const start = performance.now();
      function tick() {
        const g = document.querySelector("[data-inspector-gate]");
        window.apiFrames.push({
          t: performance.now(),
          epoch: Date.now(),
          phase: document.querySelector("[data-inspector-phase]")?.dataset.inspectorPhase,
          hidden: g?.hidden,
          hash: location.hash,
          title: !!document.querySelector(".hp-inspector-title-arrival"),
        });
        if (performance.now() - start < 4000) requestAnimationFrame(tick);
      }
      tick();
    };
  });
  await p.goto("/");
  await p.getByLabel("用户名").fill(user);
  await p.getByLabel("密码", { exact: true }).fill("r416-review-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-shell")).toBeVisible();
  return p;
}
const p = await login("r416_closure");
const tree = await (await p.request.get("/api/v1/workspace")).json(),
  node = tree.nodes.find((n) => n.node_id === chain.node);
for (const [kind, id, hash, source] of [
  ["run", chain.run, `#/ai/${chain.conv}`, "#run-open-" + chain.run],
  ["artifact", artifact, `#/ai/${chain.conv}`, "#artifact-open-" + artifact],
  ["file", chain.node, "#/workspace?dir=" + node.parent_id, "#workspace-node-" + chain.node],
  ["task", chain.task, "#/tasks?bucket=waiting&type=all", "#task-" + chain.task],
]) {
  let evidence;
  const requests = [];
  const listener = (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/"))
      requests.push({ path: new URL(r.url()).pathname, method: r.method(), epoch: Date.now() });
  };
  p.on("request", listener);
  try {
    await p.emulateMedia({ reducedMotion: "reduce" });
    await p.goto("/" + hash);
    await expect(p.locator(source)).toBeVisible();
    await p.locator(source).evaluate((n) => n.scrollIntoView({ block: "center" }));
    await p.waitForTimeout(200);
    await p.emulateMedia({ reducedMotion: "no-preference" });
    await p.evaluate(() => window.startSampling());
    const clickEpoch = Date.now();
    await p.locator(source).click();
    await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
    await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
    await expect(p.locator(".hp-inspector--" + kind)).toBeVisible();
    const data = await p.evaluate(() => ({ events: window.apiEvents, frames: window.apiFrames }));
    expect(data.events.some((e) => e.reason === "actual-tip")).toBe(true);
    evidence = {
      result: "pass",
      kind,
      id,
      clickEpoch,
      requests,
      ...data,
      cacheNote:
        kind === "task"
          ? "Task controller prefetch/coalesced query may precede click"
          : "Fresh page stores; shared source-page requests listed separately",
    };
    await p.screenshot({ path: out + "/screenshots/real-source-" + kind + ".png" });
    await p.locator('.hp-inspector button[title="关闭"]').click();
    await expect(p.locator(".hp-inspector")).toHaveCount(0);
    expect(
      await p
        .locator(".hp-inspector-exit")
        .evaluateAll((ns) =>
          ns.every(
            (n) => n.inert && !n.textContent.trim() && !n.querySelector("iframe,button,input"),
          ),
        ),
    ).toBe(true);
  } catch (e) {
    evidence = { result: "fail", kind, error: String(e), requests };
  }
  reports.push(evidence);
  p.off("request", listener);
}
await p.close();
const other = await login("r416_other");
const checks = [];
for (const [kind, id, path] of [
  ["run", chain.run, "/api/v1/runs/" + chain.run],
  ["artifact", artifact, "/api/v1/artifacts/" + artifact],
  ["file", chain.node, "/api/v1/files/" + chain.file],
  ["task", chain.task, "/api/v1/works/" + chain.task],
]) {
  try {
    const r = await other.request.get(path);
    expect(r.status()).toBe(404);
    await other.goto("/#/tasks?inspect=" + kind + "%3A" + id);
    await expect(other.locator(".hp-inspector")).toBeVisible();
    await expect(other.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
    await expect(other.locator(".hp-inspector-relay")).toHaveCount(0);
    await expect(other.locator(".hp-inspector")).toContainText(/不可用|没有权限|无法/);
    expect(await other.locator("iframe").count()).toBe(0);
    await other.screenshot({ path: out + "/screenshots/real-other-" + kind + ".png" });
    checks.push({ kind, status: r.status(), result: "pass" });
  } catch (e) {
    checks.push({ kind, result: "fail", error: String(e) });
  }
}
await other.getByRole("button", { name: "账户设置", exact: true }).click();
await other.getByRole("button", { name: "退出登录", exact: true }).click();
await expect(other.getByLabel("用户名")).toBeVisible();
await expect(
  other.locator(".hp-inspector,.hp-inspector-exit,.hp-inspector-relay,iframe"),
).toHaveCount(0);
await other.close();
await browser.close();
await writeFile(
  out + "/evidence/real-access.json",
  JSON.stringify(
    { reports, crossAccount: checks, logoutClears: true, errors, realAPI: true },
    null,
    2,
  ),
);
console.log(
  JSON.stringify(
    {
      sources: reports.map((r) => ({ kind: r.kind, result: r.result, error: r.error })),
      checks,
      errors,
    },
    null,
    2,
  ),
);
if ([...reports, ...checks].some((r) => r.result === "fail") || errors.length) process.exitCode = 1;
