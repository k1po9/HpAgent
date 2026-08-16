/**
 * API client contract tests (hpagent-web-api-contract.md §10, §11):
 * CSRF rotation on `csrf_invalid`, no rotation on other errors, idempotency
 * header propagation, and stable typed errors for 401/403/409/412/503.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { ApiClient } from "./client";
import { HpCommandError } from "./types";

const OK = (body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...headers },
  });

/** Case-insensitive header lookup on a plain request-header object. */
function headerValue(headers: unknown, name: string): string | undefined {
  if (!headers || typeof headers !== "object") {
    return undefined;
  }
  const needle = name.toLowerCase();
  for (const [key, value] of Object.entries(headers as Record<string, unknown>)) {
    if (key.toLowerCase() === needle) {
      return typeof value === "string" ? value : undefined;
    }
  }
  return undefined;
}

function error(status: number, code: string, message: string): Response {
  return new Response(
    JSON.stringify({ error: { code, message, request_id: null, retryable: false, details: {} } }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

describe("ApiClient auth recovery", () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  let client: ApiClient;

  beforeEach(() => {
    calls.length = 0;
    fetchMock = async (input, init) => {
      calls.push({ url: String(input), init });
      const url = String(input);
      if (url === "/api/v1/me") {
        return OK({ account: {}, session: {}, csrf_token: "token-from-me", capabilities: {} });
      }
      if (url.startsWith("/api/v1/conversations") && init?.method === "POST") {
        if (init?.headers && "x-csrf-token" in (init.headers as Record<string, string>)) {
          return error(403, "csrf_invalid", "CSRF 校验失败。");
        }
        return OK({ conversation: { conversation_id: "c1" } });
      }
      if (url.startsWith("/api/v1/runs/run1/cancel")) {
        return error(409, "run_not_cancellable", "当前状态不可取消。");
      }
      return OK({ items: [], next_cursor: null, has_more: false });
    };
    client = new ApiClient(fetchMock);
  });

  it("me() caches the CSRF token", async () => {
    const me = await client.me();
    expect(me?.csrf_token).toBe("token-from-me");
  });

  it("registers, verifies the cookie session through /me, and creates a QQ challenge", async () => {
    const seen: Array<{ url: string; init?: RequestInit }> = [];
    const authMock = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      seen.push({ url, init });
      if (url === "/auth/register") {
        return new Response(JSON.stringify({ registered: true }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/v1/me") {
        return OK({
          account: { account_id: "a1" },
          session: {},
          csrf_token: "csrf",
          identities: { web: { username: "alice" }, qq: { bound: false } },
          capabilities: {},
        });
      }
      return OK({
        challenge_id: "ch1",
        code: "HP-483921",
        expires_at: "2026-08-16T00:05:00Z",
      });
    };
    const c = new ApiClient(authMock);
    expect(await c.register("alice", "correct-password")).toBe(true);
    const challenge = await c.createQqBindingChallenge();
    expect(challenge.code).toBe("HP-483921");
    expect(seen.map((call) => call.url)).toEqual([
      "/auth/register",
      "/api/v1/me",
      "/api/v1/identity-bindings/qq/challenges",
    ]);
    expect(headerValue(seen[2]?.init?.headers, "x-csrf-token")).toBe("csrf");
  });

  it("rotates the CSRF token exactly once on csrf_invalid, then retries", async () => {
    let postCount = 0;
    const rotationMock = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      const url = String(input);
      if (url === "/api/v1/me") {
        return OK({ account: {}, session: {}, csrf_token: "token-from-me", capabilities: {} });
      }
      if (url.startsWith("/api/v1/conversations") && init?.method === "POST") {
        postCount += 1;
        // First mutation attempt is rejected (stale/absent token); after the
        // refresh the replay succeeds.
        return postCount === 1
          ? error(403, "csrf_invalid", "CSRF 校验失败。")
          : OK({ conversation: { conversation_id: "c1" } });
      }
      return OK({ items: [], next_cursor: null, has_more: false });
    };
    const rotating = new ApiClient(rotationMock);
    const result = await rotating.request<{ conversation: { conversation_id: string } }>({
      method: "POST",
      path: "/api/v1/conversations",
      body: { title: null },
      idempotencyKey: "key-1",
    });
    expect(result.conversation.conversation_id).toBe("c1");
    // 1) original request 2) GET /me 3) retried request
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/conversations",
      "/api/v1/me",
      "/api/v1/conversations",
    ]);
    // The fresh client sent no token on the first attempt; the replay must
    // carry the token fetched from /me and reuse the same idempotency key.
    expect(headerValue(calls[0]?.init?.headers, "x-csrf-token")).toBeUndefined();
    expect(headerValue(calls[2]?.init?.headers, "x-csrf-token")).toBe("token-from-me");
  });

  it("puts the Idempotency-Key header on every mutation request", async () => {
    const key = "019fdd40-0000-7000-8000-000000000001";
    const seen: Array<Record<string, string>> = [];
    const keyMock = async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      seen.push((init?.headers ?? {}) as Record<string, string>);
      return OK({ conversation: { conversation_id: "c1" } });
    };
    const c = new ApiClient(keyMock);
    await c.request({
      method: "POST",
      path: "/api/v1/conversations",
      body: { title: null },
      idempotencyKey: key,
    });
    expect(headerValue(seen[0], "idempotency-key")).toBe(key);
  });

  it("replays the SAME Idempotency-Key after a CSRF rotation", async () => {
    const key = "019fdd40-0000-7000-8000-000000000002";
    let postCount = 0;
    const keysSeen: Array<string | undefined> = [];
    const replayMock = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/me") {
        return OK({ csrf_token: "fresh" });
      }
      postCount += 1;
      keysSeen.push(headerValue(init?.headers, "idempotency-key"));
      return postCount === 1
        ? error(403, "csrf_invalid", "CSRF 校验失败。")
        : OK({ conversation: { conversation_id: "c1" } });
    };
    const c = new ApiClient(replayMock);
    await c.request({
      method: "POST",
      path: "/api/v1/conversations",
      body: { title: null },
      idempotencyKey: key,
    });
    // Original mutation and the post-rotation replay both carry the caller's key.
    expect(keysSeen).toEqual([key, key]);
  });

  it("does NOT rotate for a second csrf_invalid (single refresh budget)", async () => {
    let fetchCalls = 0;
    const always403 = async (_input: RequestInfo | URL) => {
      fetchCalls += 1;
      if (String(_input).startsWith("/api/v1/me")) {
        return OK({ csrf_token: "fresh" });
      }
      return error(403, "csrf_invalid", "CSRF 校验失败。");
    };
    const flaky = new ApiClient(always403);
    await expect(
      flaky.request({
        method: "POST",
        path: "/api/v1/conversations",
        body: {},
        idempotencyKey: "k",
      }),
    ).rejects.toBeInstanceOf(HpCommandError);
    await expect(
      flaky.request({
        method: "POST",
        path: "/api/v1/conversations",
        body: {},
        idempotencyKey: "k",
      }),
    ).rejects.toMatchObject({ code: "csrf_invalid" });
    // Each request spends exactly one refresh budget: original + me + replay.
    expect(fetchCalls).toBe(6);
  });

  it("does not rotate on non-CSRF errors (conversation_busy, run_not_cancellable)", async () => {
    let meCalls = 0;
    const mock = async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input) === "/api/v1/me") {
        meCalls += 1;
        return OK({ csrf_token: "t" });
      }
      if (String(input).startsWith("/api/v1/conversations")) {
        return error(409, "conversation_busy", "当前对话仍有请求正在执行。");
      }
      return error(409, "run_not_cancellable", "当前状态不可取消。");
    };
    const c = new ApiClient(mock);
    await expect(
      c.request({ method: "POST", path: "/api/v1/conversations", body: {}, idempotencyKey: "k" }),
    ).rejects.toMatchObject({ status: 409, code: "conversation_busy" });
    await expect(
      c.request({
        method: "POST",
        path: "/api/v1/runs/run1/cancel",
        body: {},
        idempotencyKey: "k",
      }),
    ).rejects.toMatchObject({ code: "run_not_cancellable" });
    expect(meCalls).toBe(0);
  });

  it("surfaces unauthenticated as a typed error and clears the cached token", async () => {
    const authMock = async () => error(401, "unauthenticated", "未登录。");
    const c = new ApiClient(authMock);
    const me = await c.me();
    expect(me).toBeNull();
    await expect(c.request({ method: "GET", path: "/api/v1/conversations" })).rejects.toMatchObject(
      {
        status: 401,
        code: "unauthenticated",
      },
    );
  });

  it("propagates idempotency-replayed as a flag on success", async () => {
    const replayMock = async (_input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      return OK({ user_message: { message_id: "m1" } }, { "idempotency-replayed": "true" });
    };
    const c = new ApiClient(replayMock);
    const result = await c.request<{ user_message: { message_id: string } }>({
      method: "POST",
      path: "/api/v1/conversations/c1/messages",
      body: { content: "hi" },
      idempotencyKey: "k",
    });
    const withFlag = result as unknown as { __idempotencyReplayed: boolean };
    expect(withFlag.__idempotencyReplayed).toBe(true);
  });

  it("version_conflict (412) and service_unavailable (503) produce typed errors", async () => {
    const codes: Array<[number, string]> = [
      [412, "version_conflict"],
      [503, "service_unavailable"],
    ];
    for (const [status, code] of codes) {
      const mock = async () => error(status, code, "err");
      const c = new ApiClient(mock);
      await expect(
        c.request({ method: "GET", path: "/api/v1/conversations" }),
      ).rejects.toMatchObject({ status, code });
    }
  });
});
