/**
 * Markdown / code rendering E2E (phase-e E-07).
 *
 * The Fake Executor's reply contains a fenced Python block; the assistant-ui
 * markdown renderer must turn it into a real <pre><code> element, not escaped
 * text.
 */
import { expect, test } from "@playwright/test";
import { createConversation, expectReply, FAKE_CODE, login, sendMessage } from "./helpers";

test("renders Markdown and fenced code in assistant replies", async ({ page }) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "显示一段代码");
  await expectReply(page);

  const code = page.locator(".hp-msg pre code").filter({ hasText: FAKE_CODE });
  await expect(code).toBeVisible();
});
