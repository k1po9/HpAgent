import { expect, test } from "@playwright/test";
import { createConversation, E2E_PASSWORD, expectReply, login, sendMessage } from "./helpers";

test("builds, interacts with, isolates and restores an Artifact", async ({ page }) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "生成可交互结果");
  await expectReply(page);

  const createdResponse = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      /\/messages\/[^/]+\/artifacts$/.test(new URL(r.url()).pathname),
  );
  await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
  const created = await (await createdResponse).json();
  const artifactId = created.artifact.artifact_id;
  const versionId = created.version.artifact_version_id;
  expect(artifactId).toBeTruthy();
  expect(versionId).toBeTruthy();
  const panel = page.locator(".hp-artifact-inspector");
  await expect(panel).toBeVisible();
  const frame = page.getByTitle("Artifact 预览");
  await expect(frame).toBeVisible({ timeout: 15_000 });
  expect(await frame.getAttribute("sandbox")).toBe("allow-scripts");
  await page.screenshot({
    path: test.info().outputPath("artifact-inspector.png"),
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
  const preview = await frame.getAttribute("srcdoc");
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await expect(page.getByPlaceholder("输入消息，Enter 发送")).toBeEnabled();

  await test.info().attach("created-artifact", {
    body: JSON.stringify({ artifactId, versionId }),
    contentType: "application/json",
  });
  const restored = page.waitForResponse(
    (r) =>
      r.request().method() === "GET" &&
      /\/messages\/[^/]+\/artifacts$/.test(new URL(r.url()).pathname),
  );
  await page.reload();
  const summaries = await (await restored).json();
  expect(summaries.items).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        artifact: expect.objectContaining({ artifact_id: artifactId }),
        latest_version: expect.objectContaining({ artifact_version_id: versionId }),
      }),
    ]),
  );
  await expect(page.locator(`#artifact-open-${artifactId}`)).toHaveAccessibleName(
    /HTML · v1 生成完成/,
  );
  await page.getByRole("button", { name: /HTML · v1 生成完成/ }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", preview!);

  // Long answers, narrow layout, reduced motion and a pending discovery read.
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const sourceMessageId = created.artifact.source_message_id;
  await page.route("**/api/v1/conversations/*/messages?*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.items = body.items.map((m: { message_id: string; content: string }) =>
      m.message_id === sourceMessageId
        ? { ...m, content: m.content + "\n\n" + "长回答末尾的成果恢复。\n\n".repeat(120) }
        : m,
    );
    await route.fulfill({ response, json: body });
  });
  let release = () => {};
  let discoveries = 0;
  let delivered = 0;
  const discoveryPath = `**/api/v1/messages/${sourceMessageId}/artifacts`;
  await page.route(discoveryPath, async (route) => {
    const response = await route.fetch();
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    discoveries++;
    await pending;
    await route.fulfill({ response });
    delivered++;
  });
  await page.reload();
  await expect.poll(() => discoveries).toBe(1);
  await expect(page.getByRole("button", { name: "生成 HTML", exact: true })).toBeDisabled();
  release();
  await expect(page.locator(`#artifact-open-${artifactId}`)).toHaveAccessibleName(
    /HTML · v1 生成完成/,
  );
  await page.locator(`#artifact-open-${artifactId}`).click();
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", preview!);
  expect(discoveries).toBe(1);
  // A response dispatched for Alice must not populate Bob's fresh account state.
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await page.reload();
  await expect.poll(() => discoveries).toBe(2);
  await page.getByRole("button", { name: "账户设置", exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await page.getByLabel("用户名").fill("bob");
  await page.getByLabel("密码").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".hp-shell")).toBeVisible();
  release();
  await expect.poll(() => delivered).toBe(2);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(page.locator(`#artifact-open-${artifactId}`)).toHaveCount(0);
  const denied = await page.request.get(`/api/v1/artifacts/${artifactId}/versions`);
  expect([403, 404]).toContain(denied.status());
});
