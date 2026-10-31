/**
 * Shared E2E helpers (phase-e E-07).
 *
 * The backend script (scripts/e2e-backend.sh) provisions the same account
 * credentials and Fake Run Executor content these helpers rely on.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

export const E2E_PASSWORD = "e2e-password";
/** First line of the Fake Run Executor's reply content. */
export const FAKE_REPLY = "这是由测试执行器生成的回复。";
/** Code line inside the Fake Run Executor's fenced code block. */
export const FAKE_CODE = "print('hello from hpagent')";
export const COMPOSER_PLACEHOLDER = "输入消息，Enter 发送";

/** A visible shell can still be inert while login/session restoration finishes. */
export async function expectWorkbenchReady(page: Page): Promise<void> {
  await expect(page.locator(".hp-shell")).toBeVisible();
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "false");
  await expect(page.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
}

/** Sign in through the real login form and wait for the workbench. */
export async function login(page: Page, username = "alice"): Promise<void> {
  await diagnoseBrowser(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "登录" }).click();
  await expectWorkbenchReady(page);
}

/** Create a fresh conversation; the composer becomes the active target. */
export async function createConversation(page: Page): Promise<void> {
  await page.getByRole("button", { name: "新建对话" }).click();
  const created = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/conversations" && r.request().method() === "POST",
  );
  // Preparing an attachment is an actual operation that needs a real ID.
  await page.getByText("选择已有文件", { exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const { conversation } = await response.json();
  await page.getByText("选择已有文件", { exact: true }).click();
  await expect(
    page.locator(`.hp-conv[data-conversation-id="${conversation.conversation_id}"]`),
  ).toHaveAttribute("aria-current", "true");
  await expect(page.getByPlaceholder(COMPOSER_PLACEHOLDER)).toBeVisible();
  await expect(page.locator(".hp-msg")).toHaveCount(0);
  await expect(page.locator('.hp-composer__attach input[type="file"]')).toBeEnabled();
}

/** Send one message and wait for its user-message bubble to appear. */
export async function sendMessage(page: Page, text: string): Promise<void> {
  const composer = page.getByPlaceholder(COMPOSER_PLACEHOLDER);
  await composer.fill(text);
  const send = page.getByRole("button", { name: "发送" });
  await expect(send).toBeEnabled();
  await send.click();
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
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeVisible();
  await expect(page.locator("[data-testid='run-label']")).toHaveText(/运行中|正在停止/);
}

const browserDiagnostics = new WeakMap<Page, unknown[]>();
test.afterEach(async ({ page }, info) => {
  const events = browserDiagnostics.get(page);
  if (!events) return;
  await info.attach("browser-diagnostics", {
    body: JSON.stringify({ baseURL: info.project.use.baseURL, url: page.url(), events }, null, 2),
    contentType: "application/json",
  });
});

/** Preserve startup failures separately from authentication assertions. */
async function diagnoseBrowser(page: Page) {
  if (browserDiagnostics.has(page)) return;
  const events: unknown[] = [];
  browserDiagnostics.set(page, events);
  const log = (kind: string, detail: unknown) => events.push({ time: Date.now(), kind, detail });
  page.on("requestfailed", (r) =>
    log("requestfailed", {
      url: r.url(),
      method: r.method(),
      resource: r.resourceType(),
      error: r.failure(),
    }),
  );
  page.on("pageerror", (e) => log("pageerror", e.message));
  page.on("console", (m) => {
    if (m.type() === "error") log("console", m.text());
  });
  page.on("crash", () => log("crash", page.url()));
  if (process.env.E2E_READING_DIAGNOSTICS !== "1") return;
  await page.addInitScript(() => {
    const record = (kind: string, detail?: unknown) => {
      const v = document.querySelector<HTMLElement>(".hp-thread__viewport");
      const rows = v?.querySelectorAll<HTMLElement>("[data-message-id]");
      console.debug(
        "READING " +
          JSON.stringify({
            time: performance.now(),
            kind,
            detail,
            top: v?.scrollTop,
            height: v?.scrollHeight,
            first: rows?.[0]?.dataset.messageId,
            last: rows?.[rows.length - 1]?.dataset.messageId,
            anchor: Array.from(rows ?? [])
              .filter(
                (n) => n.getBoundingClientRect().bottom > (v?.getBoundingClientRect().top ?? 0),
              )
              .slice(0, 1)
              .map((n) => [
                n.dataset.messageId,
                n.getBoundingClientRect().top - v!.getBoundingClientRect().top,
              ]),
            inert: Boolean(v?.closest("[inert]")),
            bottomButton: Boolean(document.querySelector(".hp-thread-bottom")),
          }),
      );
    };
    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, "scrollTop")!;
    Object.defineProperty(Element.prototype, "scrollTop", {
      ...descriptor,
      set(value) {
        if (this.matches(".hp-thread__viewport"))
          record("write", { value, stack: new Error().stack });
        descriptor.set!.call(this, value);
      },
    });
    document.addEventListener("scroll", () => record("scroll"), true);
    new MutationObserver(() => record("dom")).observe(document, { subtree: true, childList: true });
    const IO = window.IntersectionObserver;
    window.IntersectionObserver = class extends IO {
      constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
        super((entries, observer) => {
          record(
            "intersection",
            entries.map((e) => ({
              target: (e.target as HTMLElement).className,
              intersecting: e.isIntersecting,
              rect: e.boundingClientRect.toJSON(),
            })),
          );
          callback(entries, observer);
        }, options);
      }
    };
  });
  page.on("console", (m) => {
    if (m.text().startsWith("READING ")) log("reading", JSON.parse(m.text().slice(8)));
  });
}
