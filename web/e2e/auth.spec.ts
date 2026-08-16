/**
 * Auth flow E2E (phase-e E-02/E-07): wrong credentials, a successful sign-in,
 * and a sign-out that returns to the login gate.
 */
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("rejects wrong credentials with the server's safe message", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();

  await page.getByLabel("用户名").fill("alice");
  await page.getByLabel("密码").fill("wrong-password");
  await page.getByRole("button", { name: "登录" }).click();

  await expect(page.getByText("登录凭证无效。")).toBeVisible();
  await expect(page.locator(".hp-workbench")).not.toBeVisible();
});

test("signs in with valid credentials", async ({ page }) => {
  await login(page, "alice");
  await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
});

test("signs out back to the login gate", async ({ page }) => {
  await login(page, "alice");
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  // The session cookie is gone: a reload stays signed out.
  await page.reload();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
});

test("registers, auto-signs in, and can log in again", async ({ page }) => {
  const username = `e2e-register-${Date.now()}`;
  await page.goto("/");
  await page.getByRole("button", { name: "没有账号？注册" }).click();
  await expect(page.getByRole("heading", { name: "HpAgent 注册" })).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill("register-password");
  await page.getByLabel("确认密码").fill("register-password");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.locator(".hp-workbench")).toBeVisible();
  await expect(page.getByRole("button", { name: "绑定 QQ" })).toBeVisible();

  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill("register-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".hp-workbench")).toBeVisible();
});
