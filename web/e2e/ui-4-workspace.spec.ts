import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { login, createConversation } from "./helpers";
import { workspace, uploadWorkspace, fileInspector } from "./workspace-helpers";
const evidence = resolve(process.cwd(), "../artifacts/product-acceptance/ui-4");

test("directory deep links, direct children, all files, file preview and browser return", async ({
  page,
}) => {
  await login(page);
  const panel = await workspace(page);
  const name = `nested-${Date.now()}`;
  await panel.getByLabel("新目录名称").fill(name);
  await panel.getByRole("button", { name: "新建目录" }).click();
  await panel.getByRole("button", { name: `📁 ${name}`, exact: true }).click();
  const hash = new URL(page.url()).hash;
  const filename = `child-${Date.now()}.txt`;
  await uploadWorkspace(page, filename, "verified preview");
  await page.reload();
  await expect(page.getByRole("table")).toBeVisible();
  expect(new URL(page.url()).hash).toBe(hash);
  await panel.getByRole("button", { name: `📄 ${filename}`, exact: true }).click();
  await expect(fileInspector(page).getByText("verified preview", { exact: true })).toBeVisible();
  await expect(page.locator(".hp-inspector")).toHaveCount(1);
  await page.goBack();
  await expect(fileInspector(page)).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "空间路径" })
    .getByRole("button", { name: "根目录", exact: true })
    .click();
  await expect(panel.getByRole("button", { name: `📄 ${filename}`, exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "目录不存在或不可访问，已返回根目录。" }),
  ).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "空间目录" })
    .getByRole("button", { name: "全部文件" })
    .click();
  await expect(panel.getByRole("button", { name: `📄 ${filename}`, exact: true })).toBeVisible();
  await page.goto("/#/workspace?dir=00000000-0000-0000-0000-000000000000");
  await expect(panel.getByRole("table")).toBeVisible();
  await expect(page).not.toHaveURL(/dir=00000000/);
  await expect(
    page.getByRole("status").filter({ hasText: "目录不存在或不可访问，已返回根目录。" }),
  ).toBeVisible();
});

test("an existing conversation never receives an implicit upload grant", async ({ page }) => {
  await login(page);
  await createConversation(page);
  const conversationId = new URL(page.url()).hash.split("/")[2]!;
  const name = `save-only-${Date.now()}.txt`;
  await uploadWorkspace(page, name, "save only");
  const rules = await (
    await page.request.get(`/api/v1/conversations/${conversationId}/resources`)
  ).json();
  expect(rules.grants.filter((g: { name: string }) => g.name === name)).toEqual([]);
});

test("lost PUT response resumes from ready metadata and does not reupload or resave", async ({
  page,
}) => {
  await login(page);
  const panel = await workspace(page);
  await panel.getByRole("button", { name: "上传文件", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "上传到空间" });
  const name = `lost-put-${Date.now()}.txt`;
  let puts = 0,
    saves = 0;
  await page.route("**/api/v1/uploads/*/content", async (route) => {
    if (route.request().method() === "PUT") {
      puts++;
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/v1/workspace/files" && r.method() === "POST") saves++;
  });
  await dialog.getByLabel("上传长期文件").setInputFiles({
    name,
    mimeType: "text/plain",
    buffer: Buffer.from("content arrived before response was lost"),
  });
  await dialog.getByRole("button", { name: "上传并保存到 Workspace" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "继续原操作", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("未自动授权");
  expect(puts).toBe(1);
  expect(saves).toBe(1);
});

test("saved upload survives a failed grant, closes, reopens and retries authorization only", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  const id = new URL(page.url()).hash.split("/")[2]!;
  const panel = await workspace(page);
  await panel.getByRole("button", { name: "上传文件", exact: true }).click();
  let fail = true,
    puts = 0,
    saves = 0;
  page.on("request", (r) => {
    if (r.method() === "PUT") puts++;
    if (new URL(r.url()).pathname === "/api/v1/workspace/files" && r.method() === "POST") saves++;
  });
  await page.route(`**/api/v1/conversations/${id}/resources`, async (route) => {
    if (route.request().method() === "POST" && fail) {
      fail = false;
      await route.abort();
    } else await route.continue();
  });
  const dialog = page.getByRole("dialog", { name: "上传到空间" });
  const name = `grant-recovery-${Date.now()}.txt`;
  await dialog.getByLabel("上传长期文件").setInputFiles({
    name,
    mimeType: "text/plain",
    buffer: Buffer.from("saved before grant failure"),
  });
  await dialog.getByLabel(/上传后用于当前对话/).check();
  await dialog.getByLabel("目标对话").selectOption(id);
  await dialog.getByRole("button", { name: "上传并保存到 Workspace" }).click();
  await expect(dialog.getByRole("status")).toContainText("资料已保存");
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "关闭上传到空间" }).click();
  await expect(panel.getByRole("button", { name: `📄 ${name}`, exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "上传文件", exact: true }).click();
  await dialog.getByText("本次会话上传与保存记录", { exact: true }).click();
  await dialog.getByRole("button", { name: "继续此操作", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("已保存并授权");
  expect(puts).toBe(1);
  expect(saves).toBe(1);
  const grants = await (await page.request.get(`/api/v1/conversations/${id}/resources`)).json();
  expect(grants.grants.filter((g: { name: string }) => g.name === name)).toHaveLength(2);
});

test("new conversation usage creates once, restores failed authorization and sends no message", async ({
  page,
}) => {
  await login(page);
  const name = `use-${Date.now()}.txt`;
  const panel = await uploadWorkspace(page, name, "long term");
  await panel.getByRole("button", { name: `📄 ${name}`, exact: true }).click();
  await fileInspector(page).getByRole("button", { name: "在对话中使用" }).click();
  const dialog = page.getByRole("dialog", { name: "在对话中使用资料" });
  let creations = 0,
    messages = 0,
    fail = true;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/v1/conversations" && r.method() === "POST") creations++;
    if (
      /\/conversations\/[^/]+\/messages$/.test(new URL(r.url()).pathname) &&
      r.method() === "POST"
    )
      messages++;
  });
  await page.route("**/api/v1/conversations/*/resources", async (route) => {
    if (route.request().method() === "POST" && fail) {
      fail = false;
      await route.abort();
    } else await route.continue();
  });
  await dialog.getByLabel("创建新对话").check();
  await dialog.getByRole("button", { name: "确认读取授权并进入对话" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "确认读取授权并进入对话" }).click();
  await expect(page).toHaveURL(/#\/ai\//);
  expect(creations).toBe(1);
  expect(messages).toBe(0);
  await expect(page.getByRole("button", { name: `撤销 ${name} 的授权` })).toBeVisible();
});

test("permission drafts, keyboard tabs, impact invalidation and responsive object surfaces", async ({
  page,
}) => {
  test.setTimeout(180000);
  await mkdir(evidence, { recursive: true });
  await login(page);
  await createConversation(page);
  const conversationId = new URL(page.url()).hash.split("/")[2]!;
  const name = `长中文标题与LongEnglishTitle-${Date.now()}.md`;
  const panel = await uploadWorkspace(
    page,
    name,
    "# 文本预览\n\n正文与表格：\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
  );
  await panel.getByRole("button", { name: `📄 ${name}`, exact: true }).click();
  let inspector = fileInspector(page);
  await inspector.getByRole("tab", { name: "预览", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(inspector.getByRole("tab", { name: "详情", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await inspector.getByRole("tab", { name: "AI 使用范围" }).click();
  await inspector.getByLabel("目标对话").selectOption(conversationId);
  await expect(inspector.getByText(/所选主体暂无适用规则/)).toBeVisible();
  await inspector.getByText("高级写入权限", { exact: true }).click();
  await inspector.getByLabel("修改内容", { exact: true }).check();
  await inspector.getByRole("button", { name: "关闭文件详情" }).click();
  const abandon = page.getByRole("dialog", { name: "放弃未提交的权限编辑？" });
  await expect(abandon).toBeVisible();
  await abandon.getByRole("button", { name: "继续编辑" }).click();
  await expect(inspector.getByLabel("修改内容", { exact: true })).toBeChecked();
  await inspector.getByRole("button", { name: "关闭文件详情" }).click();
  await abandon.getByRole("button", { name: "放弃编辑并继续" }).click();
  await expect(inspector).toHaveCount(0);
  await panel.getByRole("button", { name: `📄 ${name}`, exact: true }).click();
  await inspector.getByRole("button", { name: "改名、移动或移除入口" }).click();
  const mutation = page.getByRole("dialog", { name: "管理空间入口" });
  await mutation.getByRole("button", { name: "预览改名或移动影响" }).click();
  await expect(mutation.getByRole("button", { name: "确认改名或移动" })).toBeVisible();
  await mutation.getByLabel("Workspace 名称").fill(`${name}.new`);
  await expect(mutation.getByRole("button", { name: "确认改名或移动" })).toHaveCount(0);
  await mutation.getByRole("button", { name: "关闭管理空间入口" }).click();
  for (const [width, height] of [
    [360, 800],
    [390, 844],
    [768, 1024],
    [1024, 768],
    [1280, 800],
    [1440, 900],
    [1920, 1080],
  ]) {
    await page.setViewportSize({ width: width!, height: height! });
    inspector = fileInspector(page);
    await expect(inspector).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({ path: resolve(evidence, `inspector-${width}x${height}.png`) });
    await inspector.getByRole("button", { name: "关闭文件详情" }).click();
    await page.screenshot({ path: resolve(evidence, `workspace-${width}x${height}.png`) });
    await panel.getByRole("button", { name: `📄 ${name}`, exact: true }).click();
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(evidence, "inspector-reduced-motion.png") });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "200%";
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: resolve(evidence, "inspector-text-200-percent.png") });
});

test("empty and unavailable objects keep navigation and focus usable", async ({ page }) => {
  await mkdir(evidence, { recursive: true });
  await login(page);
  const panel = await workspace(page);
  const name = `空目录-${Date.now()}`;
  await panel.getByLabel("新目录名称").fill(name);
  await panel.getByRole("button", { name: "新建目录" }).click();
  await panel.getByRole("button", { name: `📁 ${name}`, exact: true }).click();
  await expect(panel.getByText("此处暂无文件或目录。", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "workspace-empty-desktop.png") });
  const directoryHash = new URL(page.url()).hash;
  await page.goto(`/${directoryHash}&inspect=file:00000000-0000-0000-0000-000000000000`);
  await expect(fileInspector(page).getByText("对象不可用。", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "file-unavailable-desktop.png") });
  await fileInspector(page).getByRole("button", { name: "关闭文件详情" }).click();
  await expect(page.getByRole("heading", { name: "空间", exact: true }).last()).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(evidence, "workspace-empty-mobile.png") });
  await page.goto(`/${directoryHash}&inspect=file:00000000-0000-0000-0000-000000000000`);
  await expect(fileInspector(page)).toHaveJSProperty("open", true);
  await expect(fileInspector(page).getByText("对象不可用。", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "file-unavailable-mobile.png") });
  await page.keyboard.press("Escape");
  await expect(fileInspector(page)).toHaveCount(0);
});
