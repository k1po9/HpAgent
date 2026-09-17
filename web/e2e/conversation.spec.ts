/**
 * Long-conversation E2E (phase-e E-03/E-04/E-07): several turns in one
 * conversation, then a refresh that rebuilds the whole view from the API.
 */
import { expect, test } from "@playwright/test";
import { createConversation, expectReply, FAKE_REPLY, login, sendMessage } from "./helpers";

test("holds a long conversation and survives a refresh", async ({ page }) => {
  await login(page);
  await createConversation(page);

  await sendMessage(page, "第一条消息");
  await expectReply(page);
  await sendMessage(page, "第二条消息");
  await expectReply(page);
  await sendMessage(page, "第三条消息");
  await expectReply(page);

  // Refresh: the view is rebuilt from the API, never a local cache.
  await page.reload();
  const messages = page.locator(".hp-msg");
  await expect(messages).toHaveCount(6);
  await expect(messages).toHaveText([
    /第一条消息/,
    new RegExp(FAKE_REPLY),
    /第二条消息/,
    new RegExp(FAKE_REPLY),
    /第三条消息/,
    new RegExp(FAKE_REPLY),
  ]);
});
