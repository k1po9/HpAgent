import { expect, test } from "@playwright/test";
import { createConversation, login } from "./helpers";
import { uploadWorkspace } from "./workspace-helpers";

test("creates a child in the selected directory and explains duplicate names", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "空间", exact: true }).click();
  const panel = page.getByRole("region", { name: "空间页面" });
  await panel.getByRole("button", { name: "📁 资料", exact: true }).click();
  await expect(panel.getByLabel("新目录名称")).toHaveValue("");
  const name = `child-${Date.now()}`;
  await panel.getByLabel("新目录名称").fill(name);
  const request = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/workspace/directories") && r.request().method() === "POST",
  );
  await panel.getByRole("button", { name: "新建目录", exact: true }).click();
  expect((await request).status()).toBe(201);
  const tree = await (await page.request.get("/api/v1/workspace")).json();
  const child = tree.nodes.find((n: { name: string }) => n.name === name);
  expect(child.parent_id).toBe(tree.nodes.find((n: { name: string }) => n.name === "资料").node_id);
  await expect(panel.getByRole("button", { name: `📁 ${name}`, exact: true })).toBeVisible();
  await panel.getByLabel("新目录名称").fill(name);
  await panel.getByRole("button", { name: "新建目录", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("同名");
});

test("uploads and authorizes the current conversation, and keeps chat drafts across pages", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  const conversationId = new URL(page.url()).hash.split("/")[2];
  await page.getByPlaceholder(/输入消息/).fill("切页后保留的草稿");
  await page.getByRole("button", { name: "空间", exact: true }).click();
  const panel = page.getByRole("region", { name: "空间页面" });
  const name = `visible-next-run-${Date.now()}.txt`;
  await panel.getByRole("button", { name: "📁 资料", exact: true }).click();
  await uploadWorkspace(page, name, "next run discovers this file", conversationId);
  const rules = await (
    await page.request.get(`/api/v1/conversations/${conversationId}/resources`)
  ).json();
  expect(
    rules.grants
      .filter((g: { name: string }) => g.name === name)
      .map((g: { operation: string }) => g.operation)
      .sort(),
  ).toEqual(["list_metadata", "read_content"]);
  await page.getByRole("button", { name: "AI", exact: true }).click();
  await page.getByRole("button", { name: "对话资料", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "当前对话资料", exact: true }).getByLabel("目标对话"),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭当前对话资料" }).click();
  await page.getByRole("button", { name: "AI", exact: true }).click();
  await expect(page.getByPlaceholder(/输入消息/)).toHaveValue("切页后保留的草稿");
  await expect(page.getByRole("button", { name: `撤销 ${name} 的授权` })).toBeVisible();
});

test("creates reminder and research directly from independent pages on narrow screens", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  const title = `reminder-${Date.now()}`;
  await page.getByLabel("工作名称").fill(title);
  await page.getByLabel("目标 / 提醒内容").fill("提醒测试到账户收件箱");
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/works") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建工作", exact: true }).click();
  expect((await created).status()).toBe(201);
  await expect(page.getByRole("article", { name: title })).toBeVisible();
  await page.getByLabel("工作类型").selectOption("research_report");
  await page.getByLabel("工作名称").fill(`research-${Date.now()}`);
  await page.getByLabel("目标 / 提醒内容").fill("测试研究来源和输出");
  const research = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/works") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建工作", exact: true }).click();
  expect((await research).status()).toBe(201);
  await expect(page.getByRole("status")).toContainText("已创建");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test("revises a scheduled work and grants its own directory permissions", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  const title = `scheduled-${Date.now()}`;
  await page.getByLabel("工作名称").fill(title);
  await page.getByLabel("目标 / 提醒内容").fill("原始提醒要求");
  await page.getByLabel("执行时间").selectOption("once");
  await page.getByLabel("日期时间").fill("2027-01-01T09:00");
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/works") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建工作", exact: true }).click();
  const work = (await (await created).json()).work;
  await page.getByLabel("操作对象").selectOption({ label: title });
  await expect(page.getByLabel("目标 / 提醒内容")).toHaveValue("原始提醒要求");
  await page.getByLabel("目标 / 提醒内容").fill("已修订的提醒要求");
  await page.getByLabel("修订原因").fill("人工验证修订入口");
  await page.getByRole("button", { name: "提交修订", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已修订");
  await page.getByText("工作资料授权", { exact: true }).click();
  await page.getByLabel("选择工作").selectOption({ label: title });
  await page.getByLabel("选择长期目录或文件").selectOption({ label: "/资料" });
  await page.getByLabel("包含此目录的所有子目录与文件").check();
  await page.getByRole("button", { name: "确认授予所选权限", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "授权已生效" })).toContainText(
    "授权已生效",
  );
  const rules = await (await page.request.get(`/api/v1/works/${work.work_id}/resources`)).json();
  expect(rules.grants).toHaveLength(2);
  expect(rules.grants.every((g: { recursive: boolean }) => g.recursive)).toBe(true);
});
