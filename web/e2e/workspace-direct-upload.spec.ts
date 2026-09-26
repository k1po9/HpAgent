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
});
