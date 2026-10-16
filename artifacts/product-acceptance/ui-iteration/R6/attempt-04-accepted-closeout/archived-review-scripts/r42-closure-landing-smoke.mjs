/* global console, process, URL */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFile, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure");
const seed = JSON.parse(await readFile(out + "/evidence/control-review-seed.json", "utf8"));
const b = await chromium.launch({ args: ["--no-sandbox"] }),
  p = await b.newPage({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" }),
  errors = [],
  reports = [];
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto("http://127.0.0.1:5333/REVIEW.html");
  await expect(p.getByRole("heading", { name: "R4 整体待人审", exact: true })).toBeVisible();
  const links = await p.getByRole("link").evaluateAll((ns) => ns.map((n) => n.href));
  for (const url of [...new Set(links.map((x) => x.split("#")[0]))]) {
    const u = new URL(url);
    expect(u.hostname).toBe("127.0.0.1");
    const r = await p.request.get(url);
    expect(r.status(), url).toBe(200);
  }
  await p.screenshot({ path: out + "/screenshots/review-landing.png", fullPage: true });
  reports.push({ name: "landing-all-links", result: "pass", links: links.length });
  await p.goto("http://127.0.0.1:5335/");
  await p.getByLabel("用户名").fill("r416_controls");
  await p.getByLabel("密码", { exact: true }).fill("r416-review-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-shell")).toBeVisible();
  for (const [kind, work] of [
    ["acceptance", seed.acceptanceWork],
    ["delivery", seed.deliveryWork],
  ]) {
    await p.goto(
      "http://127.0.0.1:5335/#/tasks?bucket=attention&type=all&work=" +
        work.work_id +
        "&inspect=task%3A" +
        work.work_id +
        "&tab=outputs",
    );
    await expect(p.locator("[data-inspector-gate]")).toHaveAttribute(
      "data-inspector-gate",
      "settled",
    );
    const actual = (
      await (await p.request.get("http://127.0.0.1:5335/api/v1/works/" + work.work_id)).json()
    ).work;
    expect(actual.status).toBe("active");
    if (kind === "acceptance") {
      expect(actual.continuation.reason).toBe("user_acceptance_required");
      await expect(
        p.locator(".hp-inspector").getByRole("button", { name: "接受这份成果", exact: true }),
      ).toBeVisible();
    } else {
      expect(actual.deliveries.some((d) => d.state === "uncertain")).toBe(true);
      await expect(p.locator(".hp-inspector")).toContainText("确认");
    }
    await p.screenshot({ path: out + "/screenshots/review-pending-" + kind + ".png" });
    reports.push({
      name: "human-pending-" + kind,
      result: "pass",
      id: work.work_id,
      noMutation: true,
    });
  }
} catch (e) {
  reports.push({ result: "fail", error: String(e) });
}
await b.close();
await writeFile(out + "/evidence/landing-smoke.json", JSON.stringify({ reports, errors }, null, 2));
console.log(JSON.stringify({ reports, errors }, null, 2));
if (reports.some((r) => r.result === "fail") || errors.length) process.exitCode = 1;
