/**
 * Stop E2E (phase-e E-04/E-07).
 *
 * A live Run is cancelled from the unified composer action and the terminal snapshot
 * does not offer Retry. Cancelled Runs are intentionally not retryable; only
 * safely classified failed Runs may use the retry endpoint.
 */
import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { createConversation, login, sendMessage } from "./helpers";

test("stops a running Run without offering Retry", async ({ page }) => {
  await login(page);
  await createConversation(page);

  const accepted = page.waitForResponse(
    (r) =>
      /\/conversations\/[^/]+\/messages$/.test(new URL(r.url()).pathname) &&
      r.request().method() === "POST",
  );
  await sendMessage(page, "开始一个会运行的任务");
  const sent = await accepted;
  expect(sent.status()).toBe(202);
  const { run } = await sent.json();

  // Wait until the Run is cancellable, then stop it within the delay window.
  const stop = page.getByRole("button", { name: "停止", exact: true });
  await expect(stop).toBeEnabled();
  const cancelled = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === `/api/v1/runs/${run.run_id}/cancel` &&
      r.request().method() === "POST",
  );
  await stop.click();
  expect((await cancelled).ok()).toBe(true);

  // The cancelled terminal snapshot is authoritative and cannot be retried.
  await expect(page.locator("[data-testid='run-label']")).toHaveText("已停止", {
    timeout: 15_000,
  });
  await expect(page.locator("[data-testid='retry-run']")).toHaveCount(0);
  const snapshot = await page.request.get(`/api/v1/runs/${run.run_id}`);
  expect((await snapshot.json()).run.status).toBe("cancelled");
  const me = await (await page.request.get("/api/v1/me")).json();
  const retry = await page.request.post(`/api/v1/runs/${run.run_id}/retry`, {
    data: {},
    headers: {
      Origin: new URL(page.url()).origin,
      "X-CSRF-Token": me.csrf_token,
      "Idempotency-Key": randomUUID(),
    },
  });
  expect(retry.status()).toBe(409);
  expect((await retry.json()).error.code).toBe("run_not_retryable");
});
