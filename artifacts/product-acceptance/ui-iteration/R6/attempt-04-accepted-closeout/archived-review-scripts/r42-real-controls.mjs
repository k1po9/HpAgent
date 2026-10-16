/* global URL, location, console, process */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFile, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure"),
  seed = JSON.parse(await readFile(out + "/evidence/control-seed.json", "utf8"));
const browser = await chromium.launch({ args: ["--no-sandbox"] }),
  p = await browser.newPage({
    baseURL: "http://127.0.0.1:5335",
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "reduce",
  });
p.setDefaultTimeout(12000);
const network = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
p.on("response", (r) => {
  if (new URL(r.url()).pathname.startsWith("/api/"))
    network.push({
      path: new URL(r.url()).pathname,
      method: r.request().method(),
      status: r.status(),
      key: r.request().headers()["idempotency-key"],
    });
});
let report;
try {
  await p.goto("http://127.0.0.1:5335");
  await p.getByLabel("用户名").fill("r416_controls");
  await p.getByLabel("密码", { exact: true }).fill("r416-review-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-shell")).toBeVisible();
  const me = await (await p.request.get("/api/v1/me")).json(),
    headers = {
      Origin: "http://127.0.0.1:5335",
      "X-CSRF-Token": me.csrf_token,
      "Idempotency-Key": randomUUID(),
    };
  const workId = seed.acceptanceWork.work_id;
  const baseline = await p.request.get("/api/v1/works/" + workId);
  const oldTag = baseline.headers().etag;
  await p.evaluate(
    (id) => (location.hash = "#/tasks?inspect=task%3A" + id + "&tab=overview"),
    workId,
  );
  await expect(p.locator(".hp-task-inspector")).toBeVisible();
  await p.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }).click();
  const budget = p.getByRole("dialog", { name: "提高任务预算" });
  const limit = budget.getByRole("spinbutton").first();
  const min = Number(await limit.getAttribute("min"));
  await limit.fill(String(min + 100));
  const raised = p.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/budget"),
  );
  await budget.getByRole("button", { name: "确认提高预算", exact: true }).click();
  expect((await raised).status()).toBe(200);
  await expect(budget).toHaveCount(0);
  const conflict = await p.request.post(`/api/v1/works/${workId}/accept-result`, {
    headers: { ...headers, "If-Match": oldTag },
    data: {
      requirement_revision: seed.acceptanceWork.current_requirement_revision,
      artifact_version_id: seed.version,
    },
  });
  expect(conflict.status()).toBe(409);
  await p.getByRole("tab", { name: "成果与执行", exact: true }).click();
  await p.getByRole("button", { name: "接受这份成果", exact: true }).click();
  const dialog = p.getByRole("dialog", { name: "接受指定成果版本" });
  await expect(dialog).toContainText(seed.version);
  await p.screenshot({ path: out + "/screenshots/real-result-confirmation.png" });
  const accepted = p.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/accept-result"),
  );
  await dialog.getByRole("button", { name: "确认接受此版本", exact: true }).click();
  const response = await accepted;
  expect(response.status()).toBe(200);
  const acceptedWork = (await response.json()).work;
  expect(acceptedWork.status).toBe("completed");
  expect(
    acceptedWork.artifacts.find((a) => a.artifact_version_id === seed.version)
      .accepted_for_revision,
  ).toBe(seed.acceptanceWork.current_requirement_revision);
  await expect(dialog).toHaveCount(0);
  const acceptedRequest = response.request();
  const replay = await p.request.post(acceptedRequest.url(), {
    headers: {
      ...headers,
      "Idempotency-Key": acceptedRequest.headers()["idempotency-key"],
      "If-Match": acceptedRequest.headers()["if-match"],
    },
    data: acceptedRequest.postDataJSON(),
  });
  expect(replay.status()).toBe(200);
  expect(replay.headers()["idempotency-replayed"]).toBe("true");
  await p.screenshot({ path: out + "/screenshots/real-result-completed.png" });
  await p.evaluate(
    (id) => (location.hash = "#/tasks?inspect=task%3A" + id + "&tab=outputs"),
    seed.deliveryWork.work_id,
  );
  await expect(p.locator(".hp-task-inspector")).toContainText("发送结果待确认");
  await p.getByRole("button", { name: "确认已送达", exact: true }).click();
  const delivery = p.getByRole("dialog", { name: "确认投递决策" });
  await expect(delivery).toContainText("不代表已读");
  await p.screenshot({ path: out + "/screenshots/real-delivery-confirmation.png" });
  const resolved = p.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().includes("/deliveries/"),
  );
  await delivery.getByRole("button", { name: "确认此投递决策", exact: true }).click();
  const resolvedResponse = await resolved;
  expect(resolvedResponse.status()).toBe(200);
  const delivered = (await resolvedResponse.json()).work;
  expect(delivered.status).toBe("active");
  expect(delivered.continuation.reason).toBe("user_acceptance_required");
  expect(
    delivered.deliveries.some(
      (d) =>
        d.purpose === "fulfillment" &&
        d.state === "accepted" &&
        d.provider_receipt?.level === "explicitly_confirmed",
    ),
  ).toBe(true);
  await expect(delivery).toHaveCount(0);
  report = {
    result: "pass",
    data: "real HTTP/PG; Worker execution and lost receipt deliberately simulated by canonical services in separate controls DB; no provider/QQ",
    budgetStatus: 200,
    staleAcceptance: conflict.status(),
    acceptedWork,
    replayed: replay.headers()["idempotency-replayed"],
    deliveryConfirmationDoesNotImplyTaskCompletion: true,
    delivered,
  };
} catch (e) {
  report = { result: "fail", error: String(e) };
  console.log(String(e));
}
await p.screenshot({ path: out + "/screenshots/real-controls-final.png" }).catch(() => {});
await writeFile(
  out + "/evidence/real-controls.json",
  JSON.stringify({ report, network, errors }, null, 2),
);
console.log(report.result);
await browser.close();
if (report.result === "fail" || errors.length) process.exitCode = 1;
