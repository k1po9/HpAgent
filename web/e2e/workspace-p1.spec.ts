import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { createConversation, login, sendMessage } from "./helpers";

test("uploads, saves, moves, and downloads a Workspace entry", async ({ page }) => {
  const projectName = `项目-${Date.now()}`;
  const movedName = `归档-${Date.now()}.md`;
  await login(page);
  const workspace = page.getByRole("region", { name: "长期 Workspace" });
  await expect(workspace.getByRole("button", { name: "根目录" })).toBeVisible();
  await workspace.getByLabel("Workspace 名称").fill(projectName);
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/workspace/directories") &&
      response.request().method() === "POST",
  );
  await workspace.getByRole("button", { name: "新建目录" }).click();
  expect((await created).status()).toBe(201);
  await expect(workspace.getByRole("button", { name: `📁 ${projectName}` })).toBeVisible();
  await workspace.getByRole("button", { name: `📁 ${projectName}` }).click();

  await createConversation(page);
  const uploaded = page.waitForResponse(
    (response) => response.url().includes("/uploads/") && response.request().method() === "PUT",
  );
  await page.locator('.hp-composer__attach input[type="file"]').setInputFiles({
    name: "报告.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# P1 浏览器下载\n", "utf-8"),
  });
  expect((await uploaded).status()).toBe(200);
  await expect(page.getByText("已就绪")).toBeVisible();
  await sendMessage(page, "保存这份报告");
  await page.getByRole("button", { name: "保存到 Workspace" }).click();
  await expect(workspace.getByRole("button", { name: "📄 报告.md" })).toBeVisible();

  await workspace.getByRole("button", { name: "📄 报告.md" }).click();
  await workspace.getByLabel("Workspace 目标目录").selectOption({ label: "根目录" });
  await workspace.getByLabel("Workspace 名称").fill(movedName);
  await workspace.getByRole("button", { name: "预览改名或移动影响" }).click();
  await workspace.getByRole("button", { name: "确认改名或移动" }).click();
  await expect(workspace.getByRole("button", { name: `📄 ${movedName}` })).toBeVisible();
  const download = page.waitForEvent("download");
  await workspace
    .getByRole("button", { name: `📄 ${movedName}` })
    .locator("..")
    .getByRole("link", { name: "下载" })
    .click();
  const downloaded = await download;
  expect(downloaded.suggestedFilename()).toBe("报告.md");
  expect(await readFile(await downloaded.path(), "utf-8")).toBe("# P1 浏览器下载\n");
});
