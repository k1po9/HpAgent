import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { login } from "./helpers";
import { uploadWorkspace, fileInspector } from "./workspace-helpers";
test("uploads directly to Workspace without creating or authorizing a conversation", async ({
  page,
}) => {
  const name = `直接上传-${Date.now()}.txt`;
  const body = "Workspace direct upload\n";
  await login(page);
  const before = await (await page.request.get("/api/v1/conversations")).json();
  const panel = await uploadWorkspace(page, name, body);
  await panel.getByRole("button", { name: `📄 ${name}`, exact: true }).click();
  const inspector = fileInspector(page);
  await expect(inspector.getByText(body.trim(), { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await inspector.getByRole("link", { name: "下载", exact: true }).click();
  expect(await readFile(await (await download).path(), "utf-8")).toBe(body);
  const after = await (await page.request.get("/api/v1/conversations")).json();
  expect(after.items.map((c: { conversation_id: string }) => c.conversation_id)).toEqual(
    before.items.map((c: { conversation_id: string }) => c.conversation_id),
  );
  await expect(page.locator(".hp-approval")).toHaveCount(0);
});
