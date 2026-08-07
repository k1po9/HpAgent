/**
 * Shared E2E helpers (phase-e E-07).
 *
 * The backend script (scripts/e2e-backend.sh) provisions the same account
 * credentials and Fake Run Executor content these helpers rely on.
 */
import { expect, type Locator, type Page } from "@playwright/test";

export const E2E_PASSWORD = "e2e-password";
/** First line of the Fake Run Executor's reply content. */
export const FAKE_REPLY = "这是由测试执行器生成的回复。";
/** Code line inside the Fake Run Executor's fenced code block. */
export const FAKE_CODE = "print('hello from hpagent')";
export const COMPOSER_PLACEHOLDER = "输入消息，Enter 发送";

/** Sign in through the real login form and wait for the workbench. */
export async function login(page: Page, username = "alice"): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.locator(".hp-workbench")).toBeVisible();
}

/** Create a fresh conversation; the composer becomes the active target. */
export async function createConversation(page: Page): Promise<void> {
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByPlaceholder(COMPOSER_PLACEHOLDER)).toBeVisible();
}

/** Send one message and wait for its user-message bubble to appear. */
export async function sendMessage(page: Page, text: string): Promise<void> {
  const composer = page.getByPlaceholder(COMPOSER_PLACEHOLDER);
  await composer.fill(text);
  await composer.press("Enter");
  await expect(page.locator(".hp-msg").filter({ hasText: text }).first()).toBeVisible();
}

/**
 * Wait for the Fake Executor reply and for the Run to reach a terminal state
 * (so the composer un-gates for the next turn).
 */
export async function expectReply(page: Page, timeout = 15_000): Promise<Locator> {
  const reply = page.getByText(FAKE_REPLY, { exact: false }).last();
  await expect(reply).toBeVisible({ timeout });
  await expect(page.locator("[data-testid='run-label']")).toHaveText(/已完成|已停止|运行失败/, {
    timeout,
  });
  return reply;
}

/** Wait until the run-status strip reports a live (cancellable) Run. */
export async function expectRunLive(page: Page): Promise<void> {
  await expect(page.locator("[data-testid='stop-run']")).toBeVisible();
  await expect(page.locator("[data-testid='run-label']")).toHaveText(/运行中|正在停止/);
}
