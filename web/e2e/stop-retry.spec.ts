/**
 * Stop + Retry E2E (phase-e E-04/E-07).
 *
 * A live Run is cancelled from the run-status strip; the terminal cancelled
 * snapshot appears and Retry reuses the original user message to produce a
 * completed reply.
 */
import { expect, test } from "@playwright/test";
import { createConversation, expectReply, login, sendMessage } from "./helpers";

test("stops a running Run, then retries it to completion", async ({ page }) => {
  await login(page);
  await createConversation(page);

  await sendMessage(page, "开始一个会运行的任务");

  // Wait until the Run is cancellable, then stop it within the delay window.
  await expect(page.locator("[data-testid='stop-run']")).toBeVisible();
  await page.locator("[data-testid='stop-run']").click();

  // The cancelled terminal snapshot surfaces with a Retry action.
  await expect(page.locator("[data-testid='run-label']")).toHaveText("已停止", {
    timeout: 15_000,
  });
  await expect(page.locator("[data-testid='retry-run']")).toBeVisible();

  // Retry reuses the original user message and completes.
  await page.locator("[data-testid='retry-run']").click();
  await expectReply(page);
});
