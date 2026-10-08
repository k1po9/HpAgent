import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { login, createConversation, sendMessage, expectReply } from "./helpers";
import type { HpArtifact, HpArtifactVersion } from "../src/api/types";

const evidence = resolve(
  process.cwd(),
  "../artifacts/product-acceptance/ui-6/self-review/fix/screenshots",
);
async function createHtml(page: Page) {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "UI-6 自检修复浏览器验收");
  await expectReply(page);
  const created = page.waitForResponse(
    (r) =>
      /\/messages\/[^/]+\/artifacts$/.test(new URL(r.url()).pathname) &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
  const result = (await (await created).json()) as {
    artifact: HpArtifact;
    version: HpArtifactVersion;
  };
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30000 });
  const ready = await page.request.get(
    `/api/v1/artifact-versions/${result.version.artifact_version_id}`,
  );
  expect(ready.ok()).toBe(true);
  const { version } = (await ready.json()) as { version: HpArtifactVersion };
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  return { artifact: result.artifact, version };
}

test("network fixtures over real HTML: no-version reopen waits for fresh data and cached preview recovers after query failure", async ({
  page,
}) => {
  const { artifact, version } = await createHtml(page);
  const v8 = {
    ...version,
    artifact_version_id: "review-v8",
    version: 8,
    html: "<p>本次加载 v8</p>",
  };
  const v9 = { ...v8, artifact_version_id: "review-v9", version: 9, html: "<p>网络恢复 v9</p>" };
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let mode: "wait" | "fail" | "recover" = "wait";
  let reads = 0;
  await page.route(`**/api/v1/artifacts/${artifact.artifact_id}/versions`, async (route) => {
    reads++;
    if (mode === "wait") await gate;
    if (mode === "fail")
      return route.fulfill({
        status: 503,
        json: {
          error: {
            code: "service_unavailable",
            message: "自检查询中断",
            request_id: null,
            retryable: true,
            details: {},
          },
        },
      });
    return route.fulfill({
      json: { artifact, items: mode === "recover" ? [version, v8, v9] : [version, v8] },
    });
  });
  await page.getByRole("button", { name: /HTML · v1 生成完成/ }).click();
  await expect.poll(() => reads).toBe(1);
  expect(new URL(page.url()).hash).not.toContain("version=");
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", version.html!);
  release();
  await expect(page).toHaveURL(/version=review-v8/);
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", v8.html);
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: resolve(evidence, "reopen-fresh-v8.png"), fullPage: true });
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  mode = "fail";
  await page.getByRole("button", { name: /HTML · v8 生成完成/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "自检查询中断" })).toBeVisible();
  expect(new URL(page.url()).hash).not.toContain("version=");
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", v8.html);
  await page.screenshot({
    path: resolve(evidence, "reopen-cached-query-failure.png"),
    fullPage: true,
  });
  mode = "recover";
  await page.getByRole("button", { name: "重试同步" }).click();
  await expect(page).toHaveURL(/version=review-v9/);
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", v9.html);
  await page.screenshot({ path: resolve(evidence, "reopen-recovery-v9.png"), fullPage: true });
});

test("network fixtures over a restored real message: its HTML row converges through interrupted polling without opening details", async ({
  page,
}) => {
  const { artifact, version } = await createHtml(page);
  const running = { ...version, status: "running", html: null, completed_at: null };
  let polls = 0,
    lists = 0;
  await page.route(`**/api/v1/messages/${artifact.source_message_id}/artifacts`, (route) =>
    route.fulfill({ json: { items: [{ artifact, latest_version: running }] } }),
  );
  await page.route(`**/api/v1/artifact-versions/${version.artifact_version_id}`, (route) => {
    polls++;
    return polls === 1 ? route.abort("failed") : route.fulfill({ json: { version } });
  });
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === `/api/v1/artifacts/${artifact.artifact_id}/versions`)
      lists++;
  });
  await page.reload();
  await expect(page.getByRole("button", { name: /HTML · v1 正在生成/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /HTML · v1 生成完成/ })).toBeVisible({
    timeout: 15000,
  });
  expect(polls).toBe(2);
  expect(lists).toBe(0);
  expect(new URL(page.url()).hash).not.toContain("inspect=artifact");
  await expect(page.getByTitle("Artifact 预览")).toHaveCount(0);
  await mkdir(evidence, { recursive: true });
  await page.screenshot({
    path: resolve(evidence, "restored-message-row-completed.png"),
    fullPage: true,
  });
});
