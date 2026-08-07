/**
 * Basic accessibility E2E (phase-e E-07).
 *
 * Landmarks, labelled fields, and semantic controls — checked through the
 * browser's accessibility tree rather than raw CSS.
 */
import { expect, test } from "@playwright/test";
import { COMPOSER_PLACEHOLDER, login } from "./helpers";

test("login form exposes a heading and labelled fields", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HpAgent 登录", level: 1 })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "用户名" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "密码" })).toBeVisible();
  await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
});

test("workbench exposes headings, labelled controls, and semantic buttons", async ({ page }) => {
  await login(page);
  await expect(page.getByRole("heading", { name: "对话", level: 2 })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();

  // A composer needs an active conversation; on a clean database alice has
  // none, so create one deterministically before asserting the textbox.
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByRole("textbox", { name: COMPOSER_PLACEHOLDER })).toBeVisible();

  // New conversation appears as a semantic list of buttons. The sidebar holds
  // every conversation alice has (the DB persists across tests), so only the
  // first entry is asserted.
  await expect(page.locator(".hp-sidebar .hp-conv").first()).toBeVisible();
});
