import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { login, sendMessage, expectReply } from "./helpers";

test("finds background Work after leaving and reload, locates its source and keeps ordinary chat independent", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "新建对话" }).click();
  await sendMessage(page, "后台研究目标的来源");
  await expectReply(page);
  const cid = decodeURIComponent(new URL(page.url()).hash.split("?")[0]!.split("/")[2]!);
  const source = await page
    .locator('.hp-msg[data-role="user"]')
    .last()
    .getAttribute("data-message-id");
  const me = await (await page.request.get("/api/v1/me")).json();
  const headers = {
    "X-CSRF-Token": me.csrf_token,
    Origin: new URL(page.url()).origin,
    "Idempotency-Key": randomUUID(),
  };
  const accepted = await page.request.post("/api/v1/works", {
    headers,
    data: {
      title: `组合目标 ${randomUUID()}`,
      conversation_id: cid,
      source_message_id: source,
      requirement: {
        objective: "研究后生成 HTML",
        capability_key: "generic_work",
        spec: { schema_version: 1 },
      },
    },
  });
  expect(accepted.status()).toBe(201);
  const { work } = await accepted.json();
  let coordinator: string | null = null;
  await expect
    .poll(async () => {
      coordinator = (await (await page.request.get(`/api/v1/works/${work.work_id}`)).json()).work
        .active_coordinator_run_id;
      return coordinator;
    })
    .not.toBeNull();
  await page.getByRole("button", { name: "空间", exact: true }).click();
  await page.reload();
  await page.goto(`/#/tasks?work=${work.work_id}`);
  const inspector = page.locator(".hp-inspector");
  await expect(inspector.getByRole("heading", { name: work.title, exact: true })).toBeVisible();
  await inspector.getByRole("button", { name: "定位来源消息" }).click();
  await expect(page).toHaveURL(new RegExp(`message=${source}`));
  await expect(page.locator(`[data-message-id="${source}"]`)).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(work.title) })).toBeVisible();
  await sendMessage(page, "普通问答，不修改后台目标");
  await expectReply(page);
  const current = (await (await page.request.get(`/api/v1/works/${work.work_id}`)).json()).work;
  expect(current.current_requirement_revision).toBe(1);
  expect(current.active_coordinator_run_id).toBe(coordinator);
  await page.goto(`/#/ai/${cid}?message=unavailable&fromWork=${work.work_id}`);
  await page.getByRole("button", { name: "返回来源任务" }).click();
  await expect(
    page.locator(".hp-inspector").getByRole("heading", { name: work.title, exact: true }),
  ).toBeVisible();
});
