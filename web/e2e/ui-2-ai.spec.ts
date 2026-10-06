import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { login, createConversation, sendMessage, expectReply } from "./helpers";
const evidence = "../artifacts/product-acceptance/ui-2/screenshots";

async function grantDirectory(page: Page) {
  const me = await (await page.request.get("/api/v1/me")).json();
  const tree = await (await page.request.get("/api/v1/workspace")).json();
  const name = `撤销复验-${crypto.randomUUID()}`;
  const response = await page.request.post("/api/v1/workspace/directories", {
    data: { parent_id: tree.root_id, name },
    headers: {
      "X-CSRF-Token": me.csrf_token,
      Origin: new URL(page.url()).origin,
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
  expect(response.status()).toBe(201);
  await page.getByRole("button", { name: "使用资料", exact: true }).click();
  await page.getByRole("checkbox", { name: `${name} · 目录` }).check();
  await page.getByRole("button", { name: "确认读取授权" }).click();
  await expect(page.getByRole("button", { name: `撤销 ${name} 的授权` })).toBeVisible();
  return name;
}

async function selectConversation(page: Page, url: string) {
  await page.evaluate((target) => {
    window.location.hash = new URL(target).hash;
  }, url);
  const id = new URL(url).hash.split("/")[2];
  await expect(page.locator(`.hp-conv[data-conversation-id="${id}"]`)).toHaveAttribute(
    "aria-current",
    "true",
  );
}

test("lost DELETE responses close the dialog after authoritative empty readback", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  const name = await grantDirectory(page);
  await page.route("**/api/v1/conversations/*/resources/*", async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    await route.fetch();
    await route.abort("failed");
  });
  await page.getByRole("button", { name: `撤销 ${name} 的授权` }).click();
  await page.getByRole("button", { name: "确认撤销", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "撤销长期资料授权" })).not.toBeVisible();
  await expect(page.getByText("暂无长期资料", { exact: true })).toBeVisible();
  await expect(page.getByText("授权已撤销。", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "消息输入" })).toBeVisible();
});

test("revoke finishing during history navigation releases only its conversation lock", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  const aUrl = page.url();
  const name = await grantDirectory(page);
  await createConversation(page);
  const bUrl = page.url();
  await selectConversation(page, aUrl);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let deletes = 0;
  await page.route("**/api/v1/conversations/*/resources/*", async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    deletes++;
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: `撤销 ${name} 的授权` }).click();
  await page.getByRole("button", { name: "确认撤销", exact: true }).click();
  await expect.poll(() => deletes).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: "确认撤销", exact: true })).toBeDisabled();
  await selectConversation(page, bUrl);
  release();
  await expect(page.getByRole("button", { name: "使用资料", exact: true })).toBeEnabled();
  await selectConversation(page, aUrl);
  await expect(page.getByText("暂无长期资料", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "撤销长期资料授权" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "使用资料", exact: true })).toBeEnabled();
});

test("B sends through the real API while A's POST is pending", async ({ page }) => {
  await login(page);
  await createConversation(page);
  const aUrl = page.url();
  const aId = new URL(aUrl).hash.split("/")[2];
  await createConversation(page);
  const bUrl = page.url();
  const bId = new URL(bUrl).hash.split("/")[2];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requests: string[] = [];
  await page.route("**/api/v1/conversations/*/messages", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const id = new URL(route.request().url()).pathname.split("/")[4];
    requests.push(id);
    if (id === aId) await gate;
    await route.continue();
  });
  await selectConversation(page, aUrl);
  await page.getByRole("textbox", { name: "消息输入" }).fill("A 延迟发送");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  await selectConversation(page, bUrl);
  await sendMessage(page, "B 独立发送");
  expect(requests).toEqual([aId, bId]);
  await expectReply(page);
  release();
  await selectConversation(page, aUrl);
  await expectReply(page);
});

test("empty composer creates only on submission, handles IME, and restores per-conversation drafts", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "新建对话" }).click();
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}/empty-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${evidence}/empty-mobile.png`, fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  let creates = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/conversations" && request.method() === "POST")
      creates++;
  });
  const input = page.getByRole("textbox", { name: "消息输入" });
  await input.fill("中文首次提交");
  expect(creates).toBe(0);
  await input.dispatchEvent("compositionstart");
  await input.press("Enter");
  expect(creates).toBe(0);
  await input.dispatchEvent("compositionend");
  await input.press("Enter");
  await expect(page.locator("[data-testid=run-label]")).toHaveText(/排队中|运行中/);
  await page.screenshot({ path: `${evidence}/streaming-desktop.png`, fullPage: true });
  await expectReply(page);
  expect(creates).toBe(1);
  await expect(page.locator(".hp-msg")).toHaveCount(2);
  await expect(input).toHaveValue("");
  const firstUrl = page.url();
  await input.fill("A 的未发草稿");
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(input).toHaveValue("");
  await sendMessage(page, "B 对话");
  await expectReply(page);
  await input.fill("B 的未发草稿");
  const secondUrl = page.url();
  await page.evaluate((url) => {
    window.location.hash = new URL(url).hash;
  }, firstUrl);
  await expect(input).toHaveValue("A 的未发草稿");
  await page.evaluate((url) => {
    window.location.hash = new URL(url).hash;
  }, secondUrl);
  await expect(input).toHaveValue("B 的未发草稿");
  await page.getByRole("button", { name: "空间", exact: true }).click();
  await page.getByRole("button", { name: "AI", exact: true }).click();
  await expect(input).toHaveValue("B 的未发草稿");
});

test("a lost POST response is confirmed with the original key and does not duplicate messages", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  const requests: Array<{ key: string | undefined; body: unknown }> = [];
  let lost = true;
  await page.route("**/api/v1/conversations/*/messages", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    requests.push({
      key: route.request().headers()["idempotency-key"],
      body: route.request().postDataJSON(),
    });
    if (lost) {
      lost = false;
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  const input = page.getByRole("textbox", { name: "消息输入" });
  await input.fill("响应丢失后的确认");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "用原提交确认上次发送" })).toBeVisible();
  await expect(input).toHaveValue("响应丢失后的确认");
  await page.getByRole("button", { name: "用原提交确认上次发送" }).click();
  await expectReply(page);
  await expect(page.locator(".hp-msg")).toHaveCount(2);
  await expect(input).toHaveValue("");
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
});

test("creation completed after leaving AI does not send or navigate and keeps the draft", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "新建对话" }).click();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let sent = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/messages") && r.method() === "POST") sent++;
  });
  await page.route("**/api/v1/conversations", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const result = await route.fetch();
    await gate;
    await route.fulfill({ response: result });
  });
  await page.getByRole("textbox", { name: "消息输入" }).fill("离开时保留的首次草稿");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "新建对话" })).toBeDisabled();
  await page.getByRole("button", { name: "空间", exact: true }).click();
  release();
  await expect(page).toHaveURL(/#\/workspace/);
  expect(sent).toBe(0);
  await page.getByRole("button", { name: "AI", exact: true }).click();
  await expect(page.getByRole("button", { name: "新建对话" })).toBeEnabled();
  await page.locator(".hp-conv").first().click();
  await expect(page.getByRole("textbox", { name: "消息输入" })).toHaveValue("离开时保留的首次草稿");
});

test("attachment abandonment is confirmed and existing files are not deleted", async ({ page }) => {
  await login(page);
  await createConversation(page);
  await page
    .locator('.hp-composer__attach input[type="file"]')
    .setInputFiles({ name: "待发.txt", mimeType: "text/plain", buffer: Buffer.from("attachment") });
  await expect(page.getByText("已就绪")).toBeVisible();
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByRole("dialog", { name: "放弃本轮附件？" })).toBeVisible();
  await page.getByRole("button", { name: "继续当前对话" }).click();
  await expect(page.getByText("已就绪")).toBeVisible();
  await page.getByRole("button", { name: "新建对话" }).click();
  await page.getByRole("button", { name: "放弃本轮附件并切换" }).click();
  await expect(page).toHaveURL(/#\/ai$/);
  await expect(page.getByText("已就绪")).not.toBeVisible();
});

test("rename, read-only resources, focus return, and seven viewport layouts", async ({ page }) => {
  await login(page);
  await createConversation(page);
  const origin = new URL(page.url()).origin;
  const me = await (await page.request.get("/api/v1/me")).json();
  const created = await page.request.post("/api/v1/workspace/directories", {
    data: {
      parent_id: (await (await page.request.get("/api/v1/workspace")).json()).root_id,
      name: `UI2资料-${Date.now()}`,
    },
    headers: {
      "X-CSRF-Token": me.csrf_token,
      Origin: origin,
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
  expect(created.status()).toBe(201);
  const nodeId = (await created.json()).node_id;
  const tree = await (await page.request.get("/api/v1/workspace")).json();
  const node = tree.nodes.find((n: { node_id: string }) => n.node_id === nodeId);
  await page.getByRole("button", { name: "修改标题" }).click();
  await page.getByRole("textbox", { name: "对话标题" }).fill("长中文标题 UI-2 验收与资料读取");
  await page.getByRole("button", { name: "保存标题" }).click();
  await expect(page.getByRole("heading", { name: "长中文标题 UI-2 验收与资料读取" })).toBeVisible();
  const useResources = page.getByRole("button", { name: "使用资料", exact: true });
  await useResources.click();
  await page.getByRole("checkbox", { name: `${node.name} · 目录` }).check();
  await page.getByRole("checkbox", { name: "包含子目录与文件" }).check();
  await page.getByRole("button", { name: "确认读取授权" }).click();
  await expect(page.getByRole("button", { name: `撤销 ${node.name} 的授权` })).toBeVisible();
  await expect(useResources).toBeFocused();
  const id = new URL(page.url()).hash.split("/")[2];
  const grants = (await (await page.request.get(`/api/v1/conversations/${id}/resources`)).json())
    .grants;
  expect(
    grants
      .filter((g: { node_id: string }) => g.node_id === nodeId)
      .map((g: { operation: string }) => g.operation)
      .sort(),
  ).toEqual(["list_metadata", "read_content"]);
  await mkdir(evidence, { recursive: true });
  for (const [width, height] of [
    [360, 800],
    [390, 844],
    [768, 1024],
    [1024, 768],
    [1280, 800],
    [1440, 900],
    [1920, 1080],
  ]) {
    await page.setViewportSize({ width, height });
    await expect(page.getByRole("textbox", { name: "消息输入" })).toBeInViewport();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({ path: `${evidence}/ai-${width}x${height}.png`, fullPage: true });
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await useResources.click();
  await page.screenshot({
    path: `${evidence}/resources-mobile-reduced-motion.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭使用长期资料" }).click();
  await expect(useResources).toBeFocused();
});

test("history prepend retains the visible anchor and streaming does not pull a reader to the bottom", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  const conversationId = new URL(page.url()).hash.split("/")[2];
  const messages = Array.from({ length: 60 }, (_, index) => ({
    message_id: `history-${index}`,
    conversation_id: conversationId,
    role: "user",
    status: "accepted",
    content: `历史消息 ${index}\n${"长历史内容用于验证阅读锚点。".repeat(30)}`,
    sequence: index + 1,
    client_request_id: null,
    produced_by_run_id: null,
    created_at: "2026-10-07T00:00:00Z",
    completed_at: null,
  }));
  await page.route("**/api/v1/conversations/*/messages?*", async (route) => {
    const older = new URL(route.request().url()).searchParams.has("cursor");
    await route.fulfill({
      json: {
        items: older ? messages.slice(0, 10) : messages.slice(10),
        has_more: !older,
        next_cursor: older ? null : "older",
        conversation_last_message_seq: 60,
      },
    });
  });
  await page.reload();
  await expect(page.locator(".hp-msg")).toHaveCount(50);
  const viewport = page.locator(".hp-thread__viewport");
  await viewport.evaluate((el) => {
    el.scrollTop = 900;
  });
  const anchor = page.locator('[data-message-id="history-13"]');
  const before = await anchor.evaluate(
    (el) =>
      el.getBoundingClientRect().top -
      el.closest(".hp-thread__viewport")!.getBoundingClientRect().top,
  );
  await page.getByRole("button", { name: "加载更早的消息" }).click();
  await expect(page.locator(".hp-msg")).toHaveCount(60);
  await expect
    .poll(
      async () =>
        await anchor.evaluate(
          (el) =>
            el.getBoundingClientRect().top -
            el.closest(".hp-thread__viewport")!.getBoundingClientRect().top,
        ),
    )
    .toBeCloseTo(before, 0);
  const top = await viewport.evaluate((el) => el.scrollTop);
  await page.getByRole("textbox", { name: "消息输入" }).fill("阅读历史时的新执行");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator("[data-testid=run-label]")).toHaveText("已完成");
  await expect(page.locator(".hp-msg")).toHaveCount(62);
  expect(await viewport.evaluate((el) => el.scrollTop)).toBeCloseTo(top, 0);
  await expect(page.getByRole("button", { name: "有新消息 / 回到底部" })).toBeVisible();
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}/long-history.png`, fullPage: true });
  await page.getByRole("button", { name: "有新消息 / 回到底部" }).click();
  await expect
    .poll(
      async () => await viewport.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight),
    )
    .toBeLessThan(48);
});

test("failed attachment keeps the draft and exposes a removable error; text zoom remains usable", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  await page.route("**/api/v1/conversations/*/uploads", async (route) =>
    route.fulfill({
      status: 422,
      json: {
        error: {
          code: "unsupported_file_type",
          message: "文件类型不支持。",
          retryable: false,
          request_id: "ui2",
          details: {},
        },
      },
    }),
  );
  const input = page.getByRole("textbox", { name: "消息输入" });
  await input.fill("附件失败时的草稿");
  await page
    .locator('.hp-composer__attach input[type="file"]')
    .setInputFiles({ name: "失败.txt", mimeType: "text/plain", buffer: Buffer.from("file") });
  await expect(page.getByText("上传失败", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
  await expect(input).toHaveValue("附件失败时的草稿");
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}/attachment-failure.png`, fullPage: true });
  await page.getByRole("button", { name: "移除 失败.txt" }).click();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "200%";
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await expect(input).toBeInViewport();
  await page.screenshot({ path: `${evidence}/text-zoom-200-mobile.png`, fullPage: true });
});

test("more than thirty conversations paginate and loaded-title filtering remains local", async ({
  page,
}) => {
  await login(page);
  const me = await (await page.request.get("/api/v1/me")).json();
  const stamp = Date.now();
  const target = `分页-${stamp}-item-00`;
  for (let index = 0; index < 31; index++) {
    const response = await page.request.post("/api/v1/conversations", {
      data: { title: `分页-${stamp}-item-${String(index).padStart(2, "0")}` },
      headers: {
        Origin: new URL(page.url()).origin,
        "X-CSRF-Token": me.csrf_token,
        "Idempotency-Key": crypto.randomUUID(),
      },
    });
    expect(response.status()).toBe(201);
  }
  await page.reload();
  await expect(page.locator(".hp-conv")).toHaveCount(30);
  await page.getByRole("textbox", { name: "筛选已加载对话" }).fill(target);
  await expect(page.getByText("已加载对话中没有匹配标题，仍可加载更多。")).toBeVisible();
  await page.getByRole("button", { name: "加载更多对话" }).click();
  await expect(page.getByRole("button", { name: target, exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "筛选已加载对话" }).fill("");
  expect(await page.locator(".hp-conv").count()).toBeGreaterThan(30);
});
