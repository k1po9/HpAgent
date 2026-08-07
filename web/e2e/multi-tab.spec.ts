/**
 * Multi-tab / multi-account E2E (phase-e E-07).
 *
 * Different accounts are isolated by separate browser contexts (fresh cookie
 * jars). Two tabs sharing one account rely on the backend's single-active-Run
 * constraint (409 conversation_busy) — not BroadcastChannel — to gate a second
 * sender, and the backend stays the final arbiter.
 */
import { expect, test } from "@playwright/test";
import { createConversation, expectReply, login, sendMessage } from "./helpers";

test("accounts are isolated across browser contexts", async ({ browser }) => {
  const aliceCtx = await browser.newContext();
  const alice = await aliceCtx.newPage();
  await login(alice, "alice");
  await createConversation(alice);
  await sendMessage(alice, "alice 的秘密");
  await expectReply(alice);

  const bobCtx = await browser.newContext();
  const bob = await bobCtx.newPage();
  await login(bob, "bob");
  // bob's sidebar and threads must never expose alice's conversation.
  await expect(bob.getByText("alice 的秘密")).not.toBeVisible();
  // bob can run their own isolated conversation.
  await createConversation(bob);
  await sendMessage(bob, "bob 的消息");
  await expectReply(bob);

  await aliceCtx.close();
  await bobCtx.close();
});

test("a second tab cannot send while a Run is active (409 wins)", async ({ browser }) => {
  const ctx = await browser.newContext();
  const tabA = await ctx.newPage();
  await login(tabA, "alice");
  await createConversation(tabA);
  await sendMessage(tabA, "第一轮");
  await expectReply(tabA);

  // Second tab loads the same account; it auto-selects the same conversation.
  const tabB = await ctx.newPage();
  await tabB.goto("/");
  await expect(tabB.locator(".hp-workbench")).toBeVisible();
  await expect(tabB.getByText("第一轮")).toBeVisible();

  // tab A starts a new Run; tab B's locally-known state is stale.
  await sendMessage(tabA, "第二轮");
  await expect(tabA.locator("[data-testid='run-label']")).toHaveText(/运行中|正在停止/);

  // tab B attempts a send into the busy conversation — the API 409s and the
  // run-status strip surfaces the busy message.
  const composerB = tabB.getByPlaceholder("输入消息，Enter 发送");
  await composerB.fill("越权发送");
  await composerB.press("Enter");
  await expect(tabB.getByText("当前对话仍有请求正在执行。")).toBeVisible();

  await ctx.close();
});
