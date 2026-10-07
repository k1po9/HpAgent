import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { createConversation, login, sendMessage } from "./helpers";
import { workspace, fileInspector } from "./workspace-helpers";
test("uploads, saves, moves, and downloads a Workspace entry", async ({ page }) => {
  const projectName = `项目-${Date.now()}`;
  const movedName = `归档-${Date.now()}.md`;
  await login(page);
  const panel = await workspace(page);
  await panel.getByLabel("新目录名称").fill(projectName);
  await panel.getByRole("button", { name: "新建目录" }).click();
  await panel.getByRole("button", { name: `📁 ${projectName}`, exact: true }).click();
  await page.getByRole("button", { name: "AI", exact: true }).click();
  await createConversation(page);
  const uploaded = page.waitForResponse(
    (r) => r.url().includes("/uploads/") && r.request().method() === "PUT",
  );
  await page.locator('.hp-composer__attach input[type="file"]').setInputFiles({
    name: "报告.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# P1 浏览器下载\n"),
  });
  expect((await uploaded).status()).toBe(200);
  await sendMessage(page, "保存这份报告");
  await page.getByRole("button", { name: "保存到 Workspace" }).click();
  const dialog = page.getByRole("dialog", { name: "保存到长期文件" });
  await dialog.getByLabel("保存目录").selectOption({ label: `/${projectName}` });
  await dialog.getByRole("button", { name: "确认保存" }).click();
  await expect(dialog).not.toBeVisible();
  await workspace(page);
  await panel.getByRole("button", { name: "📄 报告.md", exact: true }).click();
  await fileInspector(page).getByRole("button", { name: "改名、移动或移除入口" }).click();
  const mutation = page.getByRole("dialog", { name: "管理空间入口" });
  await mutation.getByLabel("Workspace 目标目录").selectOption({ label: "/" });
  await mutation.getByLabel("Workspace 名称").fill(movedName);
  await mutation.getByRole("button", { name: "预览改名或移动影响" }).click();
  await mutation.getByRole("button", { name: "确认改名或移动" }).click();
  await fileInspector(page).getByRole("button", { name: "关闭文件详情" }).click();
  await panel
    .getByRole("navigation", { name: "空间路径" })
    .getByRole("button", { name: "根目录", exact: true })
    .click();
  await panel.getByRole("button", { name: `📄 ${movedName}`, exact: true }).click();
  const download = page.waitForEvent("download");
  await fileInspector(page).getByRole("link", { name: "下载", exact: true }).click();
  const downloaded = await download;
  expect(downloaded.suggestedFilename()).toBe("报告.md");
  expect(await readFile(await downloaded.path(), "utf-8")).toBe("# P1 浏览器下载\n");
});
