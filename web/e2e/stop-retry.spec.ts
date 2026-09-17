/**
 * Stop E2E (phase-e E-04/E-07).
 *
 * A live Run is cancelled from the run-status strip and the terminal snapshot
 * does not offer Retry. Cancelled Runs are intentionally not retryable; only
 * safely classified failed Runs may use the retry endpoint.
 */
import { expect, test } from "@playwright/test";
import { createConversation, login, sendMessage } from "./helpers";

test("stops a running Run without offering Retry", async ({ page }) => {
  await login(page);
  await createConversation(page);

  await sendMessage(page, "开始一个会运行的任务");

  // Wait until the Run is cancellable, then stop it within the delay window.
  await expect(page.locator("[data-testid='stop-run']")).toBeVisible();
  await page.locator("[data-testid='stop-run']").click();

  // The cancelled terminal snapshot is authoritative and cannot be retried.
  await expect(page.locator("[data-testid='run-label']")).toHaveText("已停止", {
    timeout: 15_000,
  });
  await expect(page.locator("[data-testid='retry-run']")).toHaveCount(0);
});
