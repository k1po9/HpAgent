/**
 * Markdown / code rendering E2E (phase-e E-07).
 *
 * The Fake Executor's reply exercises the Markdown structures commonly emitted
 * by an Agent, including a GFM table.
 */
import { expect, test } from "@playwright/test";
import {
  createConversation,
  expectReply,
  FAKE_CODE,
  FAKE_REPLY,
  login,
  sendMessage,
} from "./helpers";

test("renders Markdown and fenced code in assistant replies", async ({ page }) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "显示一段代码");
  await expectReply(page);

  const reply = page.locator(".hp-msg").filter({ hasText: FAKE_REPLY }).last();
  await expect(reply.locator("h2")).toHaveText("Markdown 标题");
  await expect(reply.locator("ul li")).toHaveText(["列表一", "列表二"]);
  await expect(reply.locator("strong")).toHaveText("粗体文本");
  await expect(reply.locator("p code")).toHaveText("inline code");

  const code = reply.locator("pre code").filter({ hasText: FAKE_CODE });
  await expect(code).toBeVisible();
  await expect(reply.locator("table th")).toHaveText(["A", "B"]);
  await expect(reply.locator("table td")).toHaveText(["1", "2"]);
});
