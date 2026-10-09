import { expect, test, type Page } from "@playwright/test";

const me = {
  account: { account_id: "entry-fixture", status: "active", created_at: "2026-10-10T00:00:00Z" },
  session: {},
  csrf_token: "fixture-csrf",
  identities: { web: { username: "fixture" }, qq: { bound: true } },
  capabilities: {},
};
async function fixtures(
  page: Page,
  probe: (headers: Record<string, string>) => Promise<"ok" | "expired" | "error">,
) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/me") {
      const state = await probe(route.request().headers());
      if (state === "error") {
        await route.abort("connectionfailed");
        return;
      }
      await route.fulfill({
        status: state === "ok" ? 200 : 401,
        json: state === "ok" ? me : { error: { code: "unauthorized" } },
      });
      return;
    }
    if (path === "/api/v1/workspace") {
      await route.fulfill({
        json: {
          workspace_id: "fixture",
          root_id: "root",
          nodes: [{ node_id: "root", parent_id: null, kind: "directory", name: "" }],
        },
      });
      return;
    }
    await route.fulfill({
      json: { items: [], next_cursor: null, has_more: false, messages: [], active_run: null },
    });
  });
}
async function settled(page: Page) {
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "false");
  await expect(page.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await expect(page.locator(".hp-entry-particles")).toHaveCount(0);
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
}

test("valid cookie: branded wait, actual particle pixels, stable shell and no navigation replay", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await fixtures(page, async () => {
    await gate;
    return "ok";
  });
  await page.goto("/");
  await expect(page.locator(".hp-recovery-ring")).toBeVisible();
  await expect(page.locator(".hp-shell")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("recovery-wait.png") });
  release();
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "true");
  await page.locator(".hp-shell").evaluate((el) => el.setAttribute("data-identity", "preserved"));
  await expect
    .poll(
      () =>
        page.locator(".hp-entry-particles").evaluateAll((nodes) => {
          const canvas = nodes[0] as HTMLCanvasElement | undefined;
          if (!canvas?.width) return false;
          const data = canvas
            .getContext("2d")!
            .getImageData(0, 0, canvas.width, canvas.height).data;
          for (let i = 3; i < data.length; i += 4) if (data[i]! > 0) return true;
          return false;
        }),
      { intervals: [30, 50, 50] },
    )
    .toBe(true);
  await page.screenshot({ path: testInfo.outputPath("recovery-particles.png") });
  await settled(page);
  await expect(page.locator(".hp-shell")).toHaveAttribute("data-identity", "preserved");
  await expect(page.locator("#canvas-title")).toBeFocused();
  await page.getByRole("button", { name: "空间", exact: true }).click();
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("login keeps its card through authentication, then cookie reload uses the ring", async ({
  page,
}, testInfo) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await fixtures(page, async (headers) =>
    headers.cookie?.includes("entry-fixture=valid") ? "ok" : "expired",
  );
  await page.route("**/auth/login", async (route) => {
    await gate;
    await route.fulfill({
      status: 200,
      body: "{}",
      headers: { "Set-Cookie": "entry-fixture=valid; Path=/; HttpOnly; SameSite=Lax" },
    });
  });
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("fixture");
  await page.getByLabel("密码", { exact: true }).fill("fixture-password");
  await page
    .locator(".hp-auth-card")
    .evaluate((el) => el.setAttribute("data-identity", "preserved"));
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".hp-auth-card")).toHaveAttribute("data-identity", "preserved");
  release();
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "true");
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entry-source", "login");
  await expect(page.locator(".hp-auth-card")).toHaveAttribute("data-identity", "preserved");
  await expect(page.locator(".hp-auth-submit")).toContainText("登录成功");
  await page.screenshot({ path: testInfo.outputPath("login-entry.png") });
  await settled(page);
  expect(
    (await page.context().cookies()).some((c) => c.name === "entry-fixture" && c.httpOnly),
  ).toBe(true);
  await page.reload();
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entry-source", "restore");
  await settled(page);
});

test("expired cookie and failed probe never play a success transition; retry recovers", async ({
  page,
}) => {
  let state: "error" | "expired" | "ok" = "error";
  await fixtures(page, async () => state);
  await page.goto("/");
  await expect(page.getByRole("alert")).toHaveText("无法连接 HpAgent API");
  await expect(page.locator("canvas")).toHaveCount(0);
  await expect(page.locator(".hp-shell")).toHaveCount(0);
  state = "expired";
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await expect(page.locator(".hp-shell")).toHaveCount(0);
  state = "ok";
  await page.reload();
  await settled(page);
});

test("reduced motion: static ring and no particle backing store", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await fixtures(page, async () => {
    await gate;
    return "ok";
  });
  await page.goto("/");
  await expect(page.locator(".hp-recovery-ring")).toHaveCSS("animation-name", "none");
  release();
  await settled(page);
  await expect(page.locator("#canvas-title")).toBeFocused();
});

test("mobile task deep link remains intact and resize settles safely", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixtures(page, async () => "ok");
  await page.goto("/#/tasks");
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "true");
  await page.setViewportSize({ width: 420, height: 844 });
  await settled(page);
  await expect(page).toHaveURL(/#\/tasks/);
  await expect(page.locator("#canvas-title")).toHaveText("任务");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("expiry during animation cancels the layer and cannot resurrect the workbench", async ({
  page,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await fixtures(page, async () => "ok");
  await page.route("**/api/v1/conversations?*", async (route) => {
    await gate;
    await route.fulfill({ status: 401, json: { error: { code: "unauthorized" } } });
  });
  await page.goto("/");
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "true");
  release();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await expect(page.locator(".hp-entry-particles")).toHaveCount(0);
  await expect(page.locator(".hp-shell")).toHaveCount(0);
  await page.waitForTimeout(1500);
  await expect(page.locator(".hp-shell")).toHaveCount(0);
});

test("slow verification stays in the ring; changing motion preference mid-flight settles", async ({
  page,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await fixtures(page, async () => {
    await gate;
    return "ok";
  });
  await page.goto("/");
  await expect(page.getByText("连接耗时较长，仍在验证会话…")).toBeVisible();
  await expect(page.locator(".hp-shell")).toHaveCount(0);
  release();
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "true");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await settled(page);
});
