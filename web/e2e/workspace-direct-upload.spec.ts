import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { login } from "./helpers";

test("uploads directly to Workspace without creating a conversation", async ({ page }) => {
  const name = `直接上传-${Date.now()}.txt`;
  const body = "Workspace direct upload\n";
  await login(page);
  const workspace = page.getByRole("region", { name: "长期 Workspace" });
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/uploads/") && response.request().method() === "PUT",
  );
  await workspace.getByLabel("上传到 Workspace").setInputFiles({
    name,
    mimeType: "text/plain",
    buffer: Buffer.from(body),
  });
  expect((await uploaded).status()).toBe(200);
  await expect(workspace.getByRole("button", { name: `📄 ${name}` })).toBeVisible();
  const download = page.waitForEvent("download");
  await workspace
    .getByRole("button", { name: `📄 ${name}` })
    .locator("..")
    .getByRole("link", { name: "下载" })
    .click();
  expect(await readFile(await (await download).path(), "utf-8")).toBe(body);
  await expect(page.locator(".hp-approval")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /save_persistent_file|保存持久文件/ })).toHaveCount(
    0,
  );
  const research = page.getByText("Research 输出", { exact: true });
  await research.click();
  await page.getByLabel("Task 标题").fill(`撤权-${Date.now()}`);
  await page.getByLabel("Research 目标").fill("读取选定 Workspace 文件");
  const input = page.getByLabel("Task 输入文件或目录");
  const inputId = await input.locator("option").filter({ hasText: name }).getAttribute("value");
  expect(inputId).toBeTruthy();
  await input.selectOption(inputId!);
  await page.getByRole("button", { name: "创建 Research Task" }).click();
  const taskTitle = page.getByRole("button", { name: /撤权-/ });
  await taskTitle.click();
  const grant = page
    .getByLabel("Task 资源授权")
    .locator(":scope > div")
    .filter({
      hasText: `${name} · read_content`,
    })
    .first();
  await expect(grant).toBeVisible();
  await grant.getByRole("button", { name: "撤销授权" }).click();
  await expect(grant).toHaveCount(0);
});
