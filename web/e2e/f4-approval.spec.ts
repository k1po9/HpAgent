import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

const path = `research/f4-e2e-${Date.now()}.md`;

async function attachAndSend(
  page: import("@playwright/test").Page,
  content: string,
  prompt: (sourceFileId: string) => string,
) {
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/uploads/") && response.request().method() === "PUT",
  );
  await page.locator('.hp-composer__attach input[type="file"]').setInputFiles({
    name: "f4-e2e.md",
    mimeType: "text/plain",
    buffer: Buffer.from(content),
  });
  const uploadResponse = await uploaded;
  const uploadBody = (await uploadResponse.json()) as { file: { file_id: string } };
  expect(uploadResponse.status()).toBe(200);
  await expect(page.getByText("已就绪").last()).toBeVisible();
  const composer = page.getByPlaceholder("输入消息，Enter 发送");
  await composer.fill(prompt(uploadBody.file.file_id));
  await composer.press("Enter");
  return uploadBody.file.file_id;
}

test("browser approves a durable persistent overwrite and downloads revision 2", async ({
  page,
}) => {
  const username = `f4-e2e-${Date.now()}`;
  await page.goto("/");
  await page.getByRole("button", { name: "没有账号？注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill("f4-e2e-password");
  await page.getByLabel("确认密码").fill("f4-e2e-password");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.locator(".hp-workbench")).toBeVisible();
  const bindingDialog = page.getByRole("alertdialog");
  if (await bindingDialog.isVisible()) {
    await page.getByRole("button", { name: "以后再说" }).click();
  }
  const [created] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/conversations") && response.request().method() === "POST",
    ),
    page.getByRole("button", { name: "新建对话" }).click(),
  ]);
  expect(created.status(), await created.text()).toBe(201);
  await expect(page.getByPlaceholder("输入消息，Enter 发送")).toBeVisible();

  await attachAndSend(
    page,
    "old content\n",
    (sourceFileId) =>
      `调用 save_persistent_file，把附件保存到 ${path}。source_file_id=${sourceFileId}，logical_path=${path}。`,
  );
  await expect(page.locator("[data-testid='run-label']")).toHaveText("已完成");
  const firstDestination = (
    await (await page.request.get(`/api/v1/persistent-files/${path}`)).json()
  ).destination;
  expect(firstDestination.current_revision).toBe(1);

  await attachAndSend(
    page,
    "approved new content\n",
    (sourceFileId) =>
      `调用 save_persistent_file，用附件更新 ${path}。source_file_id=${sourceFileId}，logical_path=${path}。`,
  );
  const card = page.getByTestId("approval-card");
  await expect(card).toContainText(path);
  await expect(card).toContainText("当前版本：revision 1");
  await expect(card).toContainText("等待你的确认");
  await expect(card.getByRole("button", { name: "拒绝" })).toBeVisible();
  await expect(card.getByRole("button", { name: "确认更新" })).toBeVisible();
  await expect(page.locator("[data-testid='run-label']")).toHaveText(/排队中|运行中/);

  await page.reload();
  await expect(card).toContainText("等待你的确认");
  await card.getByRole("button", { name: "确认更新" }).click();
  await expect(card).toContainText(/已批准/);
  await expect(page.locator("[data-testid='run-label']")).toHaveText("已完成");
  await expect(card).toContainText("最新版本：revision 2");

  const destinationResponse = await page.request.get(`/api/v1/persistent-files/${path}`);
  expect(destinationResponse.status()).toBe(200);
  const destination = (await destinationResponse.json()).destination;
  expect(destination).toMatchObject({
    logical_path: path,
    current_revision: 2,
    current_sha256: createHash("sha256").update("approved new content\n").digest("hex"),
  });
  expect(destination.current_file_id).not.toBe(firstDestination.current_file_id);

  const downloadPromise = page.waitForEvent("download");
  await card.getByRole("link", { name: "下载文件" }).click();
  const download = await downloadPromise;
  expect(await readFile(await download.path(), "utf8")).toBe("approved new content\n");
});
