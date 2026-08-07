/**
 * Disconnect / degraded-SSE E2E (phase-e E-06/E-07, contract §13.2).
 *
 * The events stream is aborted mid-run. The client stops delta assembly, shows
 * the degraded notice, and recovers by polling the Run with backoff until the
 * committed terminal snapshot arrives.
 */
import { expect, test } from "@playwright/test";
import { createConversation, expectReply, login, sendMessage } from "./helpers";

test("recovers from a dropped SSE connection by polling", async ({ page }) => {
  await login(page);
  await createConversation(page);

  // Abort every events subscription so the SSE stream can never survive.
  await page.route("**/api/v1/runs/*/events", (route) => route.abort());

  await sendMessage(page, "断线测试");

  // The degraded notice appears once the client notices the connection loss.
  await expect(page.locator("[data-testid='run-degraded']")).toBeVisible({ timeout: 10_000 });

  // Recovery by querying the Run still lands the completed reply, and once the
  // terminal snapshot replaces state the degraded notice clears.
  await expectReply(page);
  await expect(page.locator("[data-testid='run-degraded']")).not.toBeVisible();
});
