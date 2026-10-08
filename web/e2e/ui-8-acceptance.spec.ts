import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { login, createConversation, sendMessage, expectReply, expectRunLive } from "./helpers";

const evidence = resolve(
  process.env.HPAGENT_UI8_EVIDENCE_DIR ?? "../artifacts/product-acceptance/ui-8/browser",
);

async function instrument(page: Page) {
  await page.addInitScript(() => {
    const requests: Record<string, number> = {};
    const streams = new Map<number, string>();
    const timers = new Set<number>();
    const intervals = new Map<number, boolean>();
    const blobs = new Set<string>();
    const longTasks: number[] = [];
    let serial = 0;
    const peaks = { chat: 0, work: 0 };
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const path = new URL(input instanceof Request ? input.url : String(input), location.href)
        .pathname;
      requests[path] = (requests[path] ?? 0) + 1;
      const kind = /\/runs\/[^/]+\/events$/.test(path)
        ? "chat"
        : /\/works\/[^/]+\/events\/stream$/.test(path)
          ? "work"
          : null;
      const id = ++serial;
      const close = () => streams.delete(id);
      if (kind) {
        streams.set(id, kind);
        peaks[kind] = Math.max(peaks[kind], [...streams.values()].filter((v) => v === kind).length);
        (init?.signal ?? (input instanceof Request ? input.signal : null))?.addEventListener(
          "abort",
          close,
          { once: true },
        );
      }
      try {
        const response = await originalFetch(input, init);
        if (kind && response.body) {
          const getReader = response.body.getReader.bind(response.body);
          response.body.getReader = (() => {
            const reader = getReader();
            const read = reader.read.bind(reader);
            reader.read = async () => {
              try {
                const result = await read();
                if (result.done) close();
                return result;
              } catch (error) {
                close();
                throw error;
              }
            };
            return reader;
          }) as typeof response.body.getReader;
        } else if (kind) close();
        return response;
      } catch (error) {
        close();
        throw error;
      }
    };
    const timeout = window.setTimeout.bind(window);
    const clearTimeout = window.clearTimeout.bind(window);
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
      if (typeof handler !== "function") return timeout(handler, delay, ...args);
      const id = timeout(() => {
        timers.delete(id);
        if (typeof handler === "function") handler(...args);
      }, delay);
      if ((delay ?? 0) >= 100) timers.add(id);
      return id;
    }) as typeof window.setTimeout;
    window.clearTimeout = (id) => {
      timers.delete(id as number);
      clearTimeout(id);
    };
    const interval = window.setInterval.bind(window);
    const clearInterval = window.clearInterval.bind(window);
    window.setInterval = ((...args: Parameters<typeof window.setInterval>) => {
      const id = interval(...args);
      // Vite's development websocket heartbeat belongs to the test server.
      // Keep its count separately; production preview has no Vite client.
      intervals.set(id, !new Error().stack?.includes("/@vite/client"));
      return id;
    }) as typeof window.setInterval;
    window.clearInterval = (id) => {
      intervals.delete(id as number);
      clearInterval(id);
    };
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (blob) => {
      const url = create(blob);
      blobs.add(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      blobs.delete(url);
      revoke(url);
    };
    new PerformanceObserver((list) => {
      longTasks.push(...list.getEntries().map((entry) => entry.duration));
    }).observe({ type: "longtask", buffered: true });
    Object.assign(window, {
      ui8Metrics: () => ({
        requests,
        peaks,
        activeStreams: streams.size,
        timers: timers.size,
        intervals: [...intervals.values()].filter(Boolean).length,
        devServerIntervals: [...intervals.values()].filter((app) => !app).length,
        blobs: blobs.size,
        longTasks,
        navigation: performance.getEntriesByType("navigation").map((entry) => entry.toJSON()),
        assets: performance
          .getEntriesByType("resource")
          .filter((entry) => entry.name.includes("/assets/"))
          .map((entry) => entry.toJSON()),
      }),
    });
  });
}

async function metrics(page: Page) {
  return page.evaluate(() =>
    (window as unknown as { ui8Metrics: () => Record<string, unknown> }).ui8Metrics(),
  );
}

async function record(page: Page, name: string, data: unknown) {
  await mkdir(evidence, { recursive: true });
  await writeFile(resolve(evidence, `${name}.json`), JSON.stringify(data, null, 2) + "\n");
  await page.screenshot({ path: resolve(evidence, `${name}.png`), fullPage: true });
}

async function logout(page: Page) {
  await page.getByRole("button", { name: "账户设置", exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await expect.poll(async () => (await metrics(page)).activeStreams).toBe(0);
  await expect.poll(async () => (await metrics(page)).blobs).toBe(0);
  await expect.poll(async () => (await metrics(page)).timers).toBe(0);
  expect((await metrics(page)).intervals).toBe(0);
}

test("real API: AI to HTML to explicit workspace save retains one chat feed and releases resources", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await instrument(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await createConversation(page);
  await sendMessage(page, "UI8 综合验收：生成 HTML 并显式保存源码副本");
  await expectRunLive(page);
  for (const name of ["空间", "任务", "AI"]) {
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name, exact: true })
      .click();
  }
  await expectReply(page);
  const completed = await metrics(page);
  expect((completed.peaks as { chat: number }).chat).toBe(1);
  const chatRequests = Object.entries(completed.requests as Record<string, number>).filter(
    ([path]) => /\/runs\/[^/]+\/events$/.test(path),
  );
  expect(chatRequests).toHaveLength(1);
  expect(chatRequests[0]![1]).toBe(1);
  await page.getByRole("button", { name: "查看执行详情", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("已完成");
  await page.getByRole("button", { name: "关闭执行详情", exact: true }).click();
  await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTitle("Artifact 预览")).toHaveAttribute("sandbox", "allow-scripts");
  await page.getByRole("button", { name: "保存源码副本到空间" }).click();
  const dialog = page.getByRole("dialog", { name: "保存到长期文件" });
  const name = `UI8-${randomUUID()}.html.txt`;
  await dialog.getByLabel("保存名称").fill(name);
  const saved = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/workspace/files" &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "确认保存" }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  await expect(dialog).toHaveCount(0);
  await record(page, "real-chain-desktop", { completed, saved: await response.json() });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 HTML", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.html$/);
  await page.getByRole("button", { name: "打开空间", exact: true }).click();
  await expect(page).toHaveURL(/#\/workspace/);
  await expect(page.locator(".hp-inspector")).toContainText(name);
  await page.setViewportSize({ width: 390, height: 844 });
  await record(page, "real-chain-mobile", await metrics(page));
  await page.getByRole("button", { name: "关闭文件详情", exact: true }).click();
  await logout(page);
  await record(page, "real-chain-disposed", { metrics: await metrics(page), errors });
  expect(errors).toEqual([]);
});

test("real API: task subscriptions stay within two and logout clears timers and feeds", async ({
  page,
}) => {
  await instrument(page);
  await login(page, "bob");
  const me = await (await page.request.get("/api/v1/me")).json();
  const ids: string[] = [];
  for (let index = 0; index < 3; index++) {
    const response = await page.request.post("/api/v1/works", {
      headers: {
        Origin: new URL(page.url()).origin,
        "X-CSRF-Token": me.csrf_token,
        "Idempotency-Key": randomUUID(),
      },
      data: {
        title: `UI8 订阅 ${index}`,
        requirement: {
          objective: "核实最多两条任务订阅",
          capability_key: "reminder",
          spec: { schema_version: 1, content: "future reminder" },
          timing: {
            schema_version: 1,
            kind: "once",
            due_at: "2027-01-01T01:00:00Z",
            timezone: "Asia/Shanghai",
          },
          acceptance_criteria: [
            { id: "sent", required: true, evidence_types: ["operation_receipt"] },
          ],
        },
      },
    });
    expect(response.status(), await response.text()).toBe(201);
    ids.push((await response.json()).work.work_id);
  }
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await page.getByRole("button", { name: /等待或已计划/ }).click();
  await expect.poll(async () => ((await metrics(page)).peaks as { work: number }).work).toBe(2);
  for (const id of ids) {
    await page.evaluate((workId) => {
      location.hash = `#/tasks?bucket=waiting&work=${workId}`;
    }, id);
    await expect(page.locator(".hp-inspector")).toContainText("核实最多两条任务订阅");
  }
  const active = await metrics(page);
  expect((active.peaks as { work: number }).work).toBe(2);
  await page.getByRole("button", { name: "关闭任务详情", exact: true }).click();
  await logout(page);
  await record(page, "task-feeds-disposed", { ids, active, disposed: await metrics(page) });
});
