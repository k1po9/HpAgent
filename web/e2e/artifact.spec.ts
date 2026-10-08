import { mkdir } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { createConversation, expectReply, login, sendMessage } from "./helpers";

test("builds, interacts with, isolates and restores an Artifact", async ({ page }) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "生成可交互结果");
  await expectReply(page);

  await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
  const panel = page.locator(".hp-artifact-inspector");
  await expect(panel).toBeVisible();
  const frame = page.getByTitle("Artifact 预览");
  await expect(frame).toBeVisible({ timeout: 15_000 });
  expect(await frame.getAttribute("sandbox")).toBe("allow-scripts");
  await mkdir("../artifacts/product-acceptance/ui-6/screenshots", { recursive: true });
  await page.screenshot({
    path: "../artifacts/product-acceptance/ui-6/screenshots/artifact-inspector.png",
    fullPage: true,
  });

  const artifact = frame.contentFrame();
  await artifact.getByRole("button", { name: "切换状态" }).click();
  await expect(artifact.getByText("开启")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 HTML", exact: true }).click();
  const downloaded = await download;
  expect(downloaded.suggestedFilename()).toMatch(/-v1\.html$/);
  const stream = await downloaded.createReadStream();
  expect(stream).not.toBeNull();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString("utf8")).toBe(await frame.getAttribute("srcdoc"));
  await page.getByRole("button", { name: "保存源码副本到空间", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "保存到长期文件" });
  await dialog.getByLabel("保存名称").fill(`artifact-${Date.now()}.html`);
  await dialog.getByRole("button", { name: "确认保存" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".hp-shell")).toBeVisible();
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await expect(page.getByPlaceholder("输入消息，Enter 发送")).toBeEnabled();

  await page.reload();
  await page.getByRole("button", { name: /HTML · v1 生成完成/ }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 15_000 });
});
