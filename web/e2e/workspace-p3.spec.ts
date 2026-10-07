import { expect, test } from "@playwright/test";
import { createConversation, login, sendMessage } from "./helpers";
import { workspace, saveReadyFile, fileInspector } from "./workspace-helpers";
test("upgrades an entry in place and keeps its version when renamed", async ({ page }) => {
  await login(page);
  await createConversation(page);
  const name = `p3-${Date.now()}.md`;
  const uploaded = page.waitForResponse(
    (r) => r.url().includes("/uploads/") && r.request().method() === "PUT",
  );
  await page
    .locator('.hp-composer__attach input[type="file"]')
    .setInputFiles({ name, mimeType: "text/markdown", buffer: Buffer.from("# P3 version one\n") });
  expect((await uploaded).status()).toBe(200);
  await sendMessage(page, "保存 P3 文件");
  await page.getByRole("button", { name: "保存到 Workspace" }).click();
  await saveReadyFile(page);
  const panel = await workspace(page);
  await panel.getByRole("button", { name: `📄 ${name}`, exact: true }).click();
  const inspector = fileInspector(page);
  await inspector.getByRole("tab", { name: "版本历史" }).click();
  await expect(inspector.getByText(/当前版本：不可变入口/)).toBeVisible();
  const upgraded = page.waitForResponse(
    (r) => r.url().endsWith("/upgrade") && r.request().method() === "POST",
  );
  await inspector.getByRole("button", { name: "启用版本历史" }).click();
  expect((await upgraded).status()).toBe(200);
  await expect(inspector.getByText(/当前版本：1/)).toBeVisible();
  await expect(inspector.getByRole("link", { name: "下载历史版本 1" })).toBeVisible();
  const renamed = `renamed-${Date.now()}.md`;
  await inspector.getByRole("button", { name: "改名、移动或移除入口" }).click();
  const mutation = page.getByRole("dialog", { name: "管理空间入口" });
  await mutation.getByLabel("Workspace 名称").fill(renamed);
  await mutation.getByRole("button", { name: "预览改名或移动影响" }).click();
  await mutation.getByRole("button", { name: "确认改名或移动" }).click();
  await expect(panel.getByRole("button", { name: `📄 ${renamed}`, exact: true })).toBeVisible();
  await expect(inspector.getByText(/当前版本：1/)).toBeVisible();
});
