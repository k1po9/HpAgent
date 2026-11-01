import { expect, test, type Page } from "@playwright/test";
import contract from "../../../test/fixtures/work/generic_completion.json" with { type: "json" };
import { workFixture } from "../../src/components/tasks/taskFixtures";
import { selectTask } from "../form-helpers";
const genericRequirement = { ...contract.new_requirement, completion_mode: "deliverable" as const };

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
  await page.route(/ipwho\.is|ipwhois\.io/, (route) => route.abort());
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
    if (path === "/api/v1/workspace" || path === "/api/v1/workspace/tree") {
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

for (const width of [1536, 390]) {
  test(`Generic editor creates a supported output contract at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await fixtures(page, async () => "ok");
    let submitted: unknown;
    await page.route(/\/api\/v1\/works(?:\/|\?|$)/, async (route) => {
      if (route.request().method() === "POST") {
        submitted = route.request().postDataJSON().requirement;
        await route.fulfill({
          status: 201,
          json: { work: workFixture({ requirement: genericRequirement }) },
        });
      } else await route.fulfill({ json: { items: [], next_before: null } });
    });
    await page.goto("/#/tasks");
    await settled(page);
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    const form = page.getByRole("dialog", { name: "新建任务", exact: true });
    await page.getByLabel("工作名称", { exact: true }).fill("文字成果");
    await selectTask(page, form, "工作类型", "通用任务");
    await page
      .getByLabel("目标 / 提醒内容", { exact: true })
      .fill(contract.new_requirement.objective);
    await selectTask(page, form, "计划时区", "UTC");
    await page.getByRole("button", { name: "创建工作", exact: true }).click();
    await expect.poll(() => submitted).toEqual(contract.new_requirement);
  });

  test(`Generic legacy upgrade requires consent and sends only a revision at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await fixtures(page, async () => "ok");
    let work = workFixture({
      status: "paused",
      requirement: { ...genericRequirement, acceptance_criteria: contract.legacy_criteria },
    });
    const writes: Array<{ path: string; headers: Record<string, string>; body: unknown }> = [];
    await page.route(/\/api\/v1\/works(?:\/|\?|$)/, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "POST") {
        writes.push({ path, headers: request.headers(), body: request.postDataJSON() });
        work = {
          ...work,
          row_version: 2,
          current_requirement_revision: 3,
          requirement: { ...work.requirement, acceptance_criteria: contract.upgraded_criteria },
        };
      }
      await route.fulfill({
        json:
          path === `/api/v1/works/${work.work_id}` || path.endsWith("/revisions")
            ? { work }
            : path === "/api/v1/works"
              ? { items: [work], next_before: null }
              : { items: [] },
      });
    });
    await page.goto(`/#/tasks?work=${work.work_id}`);
    await settled(page);
    await page
      .locator(".hp-inspector")
      .getByRole("button", { name: "修改要求", exact: true })
      .click();
    const consent = page.getByRole("checkbox", {
      name: "我确认将操作回执条件升级为已发布成果条件",
    });
    await expect(consent).not.toBeChecked();
    await page.getByLabel("修订原因", { exact: true }).fill("确认已发布成果验收");
    await page.getByRole("button", { name: "提交修订", exact: true }).click();
    await expect(page.getByText("请先确认将旧操作回执条件升级为已发布成果条件。")).toBeVisible();
    expect(writes).toHaveLength(0);
    await consent.check();
    await page.getByRole("button", { name: "提交修订", exact: true }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toMatchObject({
      path: `/api/v1/works/${work.work_id}/revisions`,
      headers: { "if-match": '"work-w1-v1"' },
      body: {
        change_reason: "确认已发布成果验收",
        requirement: { acceptance_criteria: contract.upgraded_criteria },
      },
    });
    expect(work.status).toBe("paused");
    await expect(page.getByRole("checkbox", { name: /我确认将操作回执/ })).toHaveCount(0);
  });
}

test("valid cookie: branded wait, actual particle pixels, stable shell and no navigation replay", async ({
  page,
}, testInfo) => {
  // This case checks animated pixels; fixture defaults otherwise request reduced motion.
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await fixtures(page, async () => {
    await gate;
    return "ok";
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HpAgent 会话恢复" })).toBeVisible();
  await expect(page.getByLabel("密码", { exact: true })).toHaveCount(0);
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

test("login keeps its card through authentication, then cookie reload restores the session", async ({
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

async function signedOutFixtures(page: Page) {
  await page.route(/ipwho\.is|ipwhois\.io/, (route) => route.abort());
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ status: 401, json: { error: { code: "unauthorized" } } }),
  );
}

test("identity terminal stays usable without WebGL, and rejection keeps the form", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      kind: string,
      ...rest: unknown[]
    ) {
      if (kind.startsWith("webgl")) return null;
      return (original as (...args: unknown[]) => unknown).call(this, kind, ...rest);
    } as typeof original;
  });
  await signedOutFixtures(page);
  await page.route("**/auth/login", (route) =>
    route.fulfill({
      status: 401,
      json: {
        error: {
          code: "invalid_credentials",
          message: "用户名或密码错误。",
          retryable: false,
          details: {},
        },
      },
    }),
  );
  await page.goto("/");
  await expect(page.locator(".hp-auth-layout")).toHaveAttribute("data-scene", "fallback");
  await expect(page.locator("canvas")).toBeHidden();
  const username = page.getByLabel("用户名", { exact: true });
  await username.fill("fixture");
  await page.getByLabel("密码", { exact: true }).fill("fixture-password");
  await username.evaluate((el) => el.setAttribute("data-identity", "preserved"));
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("用户名或密码错误。");
  await expect(page.locator(".hp-auth-layout")).toHaveAttribute("data-denied", "true");
  await expect(username).toHaveAttribute("data-identity", "preserved");
  await expect(username).toHaveValue("fixture");
  await expect(page.locator(".hp-auth-layout")).not.toHaveAttribute("data-denied", "true");
});

test("registration keeps the file's size and the profile shows the lookup or its absence", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.route("https://ipwho.is/**", (route) =>
    route.fulfill({
      json: {
        success: true,
        ip: "203.0.113.9",
        country: "中国",
        region: "上海",
        city: "上海",
        country_code: "CN",
        latitude: 31.23,
        longitude: 121.47,
        connection: { isp: "Example Net" },
      },
      headers: { "access-control-allow-origin": "*" },
    }),
  );
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ status: 401, json: { error: { code: "unauthorized" } } }),
  );
  await page.goto("/");
  const card = page.locator(".hp-auth-card");
  await expect(card.locator(".profile")).toContainText("203.0.113.9");
  await expect(card.locator(".profile")).toContainText("中国 上海");
  await expect(card.locator(".profile dt")).toHaveText([
    "MODE",
    "OPENED AT",
    "IP",
    "REGION",
    "ATTEMPT",
  ]);
  await expect(page.locator(".hp-identity-callouts")).toContainText("≈ 31.23°N 121.47°E");
  const login = (await card.boundingBox())!.height;
  await page.getByRole("button", { name: "没有账号？注册" }).click();
  await expect(page.getByLabel("确认密码")).toBeVisible();
  await expect.poll(async () => (await card.boundingBox())!.height).toBeCloseTo(login, 0);
  await expect(page.getByRole("button", { name: "注册", exact: true })).toBeInViewport();
  await page.getByLabel("用户名", { exact: true }).focus();
  for (const label of ["密码", "确认密码", "邀请码（可选）"]) {
    await page.keyboard.press("Tab");
    await expect(page.getByLabel(label, { exact: true })).toBeFocused();
  }
});

test("a rate-limited lookup leaves the profile unavailable and the form usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.route("https://ipwho.is/**", (route) => route.fulfill({ status: 429, body: "limit" }));
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ status: 401, json: { error: { code: "unauthorized" } } }),
  );
  await page.goto("/");
  await expect(page.locator(".hp-auth-card .profile")).toContainText("UNAVAILABLE");
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeEnabled();
  await expect(page.locator(".hp-identity-flag")).toHaveCount(0);
});

test("identity terminal holds no animation loop under reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    const original = window.requestAnimationFrame.bind(window);
    const counter = window as unknown as { rafCalls: number };
    counter.rafCalls = 0;
    window.requestAnimationFrame = (callback) => {
      counter.rafCalls += 1;
      return original(callback);
    };
  });
  await signedOutFixtures(page);
  await page.goto("/");
  const layout = page.locator(".hp-auth-layout");
  await expect(layout).toHaveAttribute("data-motion", "off");
  await expect(layout).toHaveAttribute("data-scene", /ready|fallback/, { timeout: 30_000 });
  await page.waitForTimeout(300);
  const calls = () => page.evaluate(() => (window as unknown as { rafCalls: number }).rafCalls);
  const before = await calls();
  await page.waitForTimeout(600);
  // A live 60Hz loop would add ~36 here; allow other components' one-off frames.
  expect((await calls()) - before).toBeLessThan(5);
});

for (const [width, height] of [
  [390, 844],
  [390, 480],
  [1280, 420],
  [1099, 900],
  [1100, 620],
] as const) {
  test(`registration reaches submit by scrolling at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await signedOutFixtures(page);
    await page.goto("/");
    await page.getByRole("button", { name: "没有账号？注册" }).click();
    const submit = page.getByRole("button", { name: "注册", exact: true });
    await page
      .locator(".hp-auth-surface")
      .evaluate((surface) => surface.scrollTo(0, surface.scrollHeight));
    await expect(submit).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}

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
  await page.addInitScript(() => localStorage.setItem("hpagent.entry-effect", "off"));
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

test("entry effect defaults on, persists off across reloads and can be re-enabled", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await fixtures(page, async () => "ok");
  await page.goto("/#/tasks");
  await expect(page.getByRole("heading", { name: "HpAgent 会话恢复" })).toBeVisible();
  await expect(page.getByLabel("密码", { exact: true })).toHaveCount(0);
  await settled(page);
  const toggle = page.getByRole("button", { name: "每次打开显示登录特效" });
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0);

  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/v1/me", async (route) => {
    await gate;
    await route.fulfill({ json: me });
  });
  await page.reload();
  await expect(page.locator(".hp-recovery-ring")).toBeVisible();
  await expect(page.locator(".hp-auth-layout")).toHaveCount(0);
  release();
  await settled(page);
  await expect(page).toHaveURL(/#\/tasks/);
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "HpAgent 会话恢复" })).toBeVisible();
  await settled(page);
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
});

test("mobile task deep link remains intact and resize settles safely", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixtures(page, async () => "ok");
  await page.goto("/#/tasks");
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "true");
  await page.setViewportSize({ width: 420, height: 844 });
  await settled(page);
  await expect(page).toHaveURL(/#\/tasks/);
  await expect(page.locator("#canvas-title")).toHaveText("任务中心");
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
  await page.addInitScript(() => localStorage.setItem("hpagent.entry-effect", "off"));
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

// Migrated from r13-default-check: product builds must not expose historical variant controls.
for (const width of [1680, 390]) {
  test(`production ignores review variants and exposes no candidate controls at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await fixtures(page, async () => "ok");
    await page.goto("/?r1=s1&r12=baseline");
    await settled(page);
    await expect(page.locator(".hp-r1-controls")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.dataset.r1)).toBeUndefined();
    expect(await page.evaluate(() => document.documentElement.dataset.r12)).toBeUndefined();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width + 1,
    );
  });
}

for (const reason of ["no_progress", "repeated_failure"] as const) {
  test(`progress protection ${reason} requires intervention on desktop and mobile`, async ({
    page,
  }) => {
    const { workFixture } = await import("../../src/components/tasks/taskFixtures");
    const work = workFixture({
      continuation: { kind: "blocked", reason },
      progress: {
        policy_version: 1,
        failure_limit: 3,
        no_progress_limit: 3,
        consecutive_failures: reason === "repeated_failure" ? 3 : 0,
        no_progress_count: 3,
        blocked_reason: reason,
      },
    });
    await fixtures(page, async () => "ok");
    await page.route("**/api/v1/works**", (route) => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({
        json:
          path === `/api/v1/works/${work.work_id}`
            ? { work }
            : path === "/api/v1/works"
              ? { items: [work], next_before: null }
              : { items: [] },
      });
    });
    await page.goto(`/#/tasks?work=${work.work_id}`);
    await settled(page);
    for (const width of [1536, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      const inspector = page.locator(".hp-inspector");
      await expect(
        inspector.getByText(
          reason === "no_progress"
            ? "持续执行没有新进展，请修改要求或补充资料"
            : "连续执行失败，请调整要求后重试",
          { exact: true },
        ),
      ).toBeVisible();
      await expect(inspector.getByRole("button", { name: "继续执行", exact: true })).toHaveCount(0);
      await expect(inspector.getByRole("button", { name: "修改要求", exact: true })).toBeVisible();
    }
  });
}
