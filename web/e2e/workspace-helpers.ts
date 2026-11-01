import { expect, type Page } from "@playwright/test";
import { selectTask } from "./form-helpers";

/** Give the target a unique visible name before choosing it from the upload form. */
export async function nameConversation(page: Page, title: string) {
  await page.locator("#canvas-title").getByRole("button").dblclick();
  await page.getByRole("textbox", { name: "对话标题", exact: true }).fill(title);
  await page.getByRole("button", { name: "保存标题", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "对话标题", exact: true })).toHaveCount(0);
  await expect(
    page.locator("#canvas-title").getByRole("button", { name: title, exact: true }),
  ).toBeVisible();
}

export async function workspace(page: Page) {
  await page.getByRole("button", { name: "空间", exact: true }).click();
  await expect(page.getByRole("table", { name: "空间文件" })).toBeVisible();
  return page.getByRole("region", { name: "空间页面" });
}
export async function uploadWorkspace(
  page: Page,
  name: string,
  body: string,
  conversationTitle?: string,
) {
  const panel = await workspace(page);
  await panel.getByRole("button", { name: "上传文件", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "上传到空间" });
  await dialog.locator('input[type="file"]').setInputFiles({
    name,
    mimeType: name.endsWith(".md") ? "text/markdown" : "text/plain",
    buffer: Buffer.from(body),
  });
  if (conversationTitle) {
    const useForConversation = dialog.getByRole("checkbox", { name: /上传后用于当前对话/ });
    await useForConversation.check();
    await expect(useForConversation).toBeChecked();
    await selectTask(page, dialog, "目标对话", conversationTitle);
  }
  await dialog.getByRole("button", { name: "上传并保存到 Workspace", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText(
    conversationTitle ? "已保存并授权" : "未自动授权",
  );
  await dialog.getByRole("button", { name: "关闭上传到空间" }).click();
  await expect(panel.getByRole("button", { name: `${name}`, exact: true })).toBeVisible();
  return panel;
}
export function fileInspector(page: Page) {
  return page.locator(".hp-inspector");
}
export async function saveReadyFile(page: Page) {
  const dialog = page.getByRole("dialog", { name: "保存到长期文件" });
  await dialog.getByRole("button", { name: "确认保存" }).click();
  await expect(dialog).not.toBeVisible();
}
