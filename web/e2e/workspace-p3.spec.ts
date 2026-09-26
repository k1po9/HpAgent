import { expect, test } from "@playwright/test";
import { createConversation, login, sendMessage } from "./helpers";

test("upgrades an entry in place and keeps its version when renamed", async ({ page }) => {
  await login(page);
  await createConversation(page);
  const name = `p3-${Date.now()}.md`;
  const uploaded = page.waitForResponse(
    (response) => response.url().includes("/uploads/") && response.request().method() === "PUT",
  );
  await page.locator('.hp-composer__attach input[type="file"]').setInputFiles({
    name,
    mimeType: "text/markdown",
    buffer: Buffer.from("# P3 version one\n"),
  });
  expect((await uploaded).status()).toBe(200);
  await sendMessage(page, "保存 P3 文件");
  await page.getByRole("button", { name: "保存到 Workspace" }).click();
  const workspace = page.getByRole("region", { name: "长期 Workspace" });
  await workspace.getByRole("button", { name: `📄 ${name}` }).click();
  await expect(workspace.getByText("当前版本：不可变入口")).toBeVisible();
  const upgraded = page.waitForResponse(
    (response) => response.url().endsWith("/upgrade") && response.request().method() === "POST",
  );
  await workspace.getByRole("button", { name: "启用版本历史" }).click();
  expect((await upgraded).status()).toBe(200);
  await expect(workspace.getByText(/当前版本：1/)).toBeVisible();
  await expect(workspace.getByText(/v1 · 上传/)).toBeVisible();
  const renamed = `renamed-${Date.now()}.md`;
  await workspace.getByLabel("Workspace 名称").fill(renamed);
  await workspace.getByRole("button", { name: "预览改名或移动影响" }).click();
  await workspace.getByRole("button", { name: "确认改名或移动" }).click();
  await expect(workspace.getByRole("button", { name: `📄 ${renamed}` })).toBeVisible();
  await workspace.getByRole("button", { name: `📄 ${renamed}` }).click();
  await expect(workspace.getByText(/当前版本：1/)).toBeVisible();
});
