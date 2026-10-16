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

test("selects and uploads a UTF-8 Markdown file, then attaches it to a message", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);

  const created = page.waitForResponse(
    (response) => response.url().endsWith("/uploads") && response.request().method() === "POST",
  );
  const uploaded = page.waitForResponse(
    (response) => response.url().includes("/uploads/") && response.request().method() === "PUT",
  );
  await page.locator('.hp-composer__attach input[type="file"]').setInputFiles({
    name: "中文笔记.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 标题\n中文内容。\n", "utf-8"),
  });
  const createResponse = await created;
  expect(createResponse.status()).toBe(201);
  expect(createResponse.request().postDataJSON().content_type).toBe("text/markdown");
  const uploadResponse = await uploaded;
  expect(uploadResponse.status()).toBe(200);
  const file = (await uploadResponse.json()).file;
  expect(file.encoding).toBe("utf-8");
  await expect(page.getByText("已就绪")).toBeVisible();

  const sent = page.waitForResponse(
    (response) => response.url().endsWith("/messages") && response.request().method() === "POST",
  );
  await sendMessage(page, "读取附件");
  const sendResponse = await sent;
  expect(sendResponse.status()).toBe(202);
  expect(sendResponse.request().postDataJSON().file_ids).toContain(file.file_id);
  await expect(page.getByText("中文笔记.md").first()).toBeVisible();
});
