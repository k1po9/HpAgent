import { expect, test } from "@playwright/test";
import { createConversation, expectReply, login, sendMessage } from "./helpers";

test("builds, interacts with, isolates and restores an Artifact", async ({ page }) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "生成可交互结果");
  await expectReply(page);

  await page.getByRole("button", { name: "生成 Artifact" }).click();
  const panel = page.getByRole("complementary", { name: "Artifact 面板" });
  await expect(panel).toBeVisible();
  const frame = page.getByTitle("Artifact 预览");
  await expect(frame).toBeVisible({ timeout: 15_000 });
  expect(await frame.getAttribute("sandbox")).toBe("allow-scripts");

  const artifact = frame.contentFrame();
  await artifact.getByRole("button", { name: "切换状态" }).click();
  await expect(artifact.getByText("开启")).toBeVisible();
  await expect(page.locator(".hp-workbench")).toBeVisible();
  await expect(page.getByPlaceholder("输入消息，Enter 发送")).toBeEnabled();

  await page.reload();
  await page.getByRole("button", { name: "生成 Artifact" }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 15_000 });
});
