/**
 * Workbench render smoke test.
 *
 * Mounts the whole auth-gated app against a stubbed fetch: a signed-in /me,
 * one conversation, and an empty message page. Verifies the assistant-ui chat
 * surface renders (sidebar + composer) without runtime errors.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useAuth } from "./store/auth";

const ME = {
  account: { account_id: "alice", status: "active", created_at: "2026-08-08T00:00:00Z" },
  session: { expires_at: "2026-08-09T00:00:00Z", idle_expires_at: "2026-08-08T01:00:00Z" },
  csrf_token: "t",
  identities: { web: { username: "alice" }, qq: { bound: false } },
  capabilities: {},
};

const CONVERSATION = {
  conversation_id: "c1",
  title: "测试对话",
  status: "active",
  last_message_seq: 0,
  metadata_version: 1,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
  useAuth.setState({
    status: "checking",
    account: null,
    identities: null,
    justRegistered: false,
  });
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/me") return json(ME);
    if (url.startsWith("/api/v1/conversations?") || url === "/api/v1/conversations") {
      return json({ items: [CONVERSATION], next_cursor: null, has_more: false });
    }
    if (/^\/api\/v1\/conversations\/c1$/.test(url)) {
      return json({ conversation: CONVERSATION, active_run: null });
    }
    if (/^\/api\/v1\/conversations\/c1\/messages\?/.test(url)) {
      return json({
        items: [],
        next_cursor: null,
        has_more: false,
        conversation_last_message_seq: 0,
      });
    }
    return json({ items: [], next_cursor: null, has_more: false });
  });
});

describe("App workbench", () => {
  it("renders the conversation list and the composer after signing in", async () => {
    render(<App />);
    expect(await screen.findByText("测试对话")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument();
    expect(screen.getByText("新建", { selector: "button" })).toBeInTheDocument();
    expect(screen.getByText("绑定 QQ", { selector: "button" })).toBeInTheDocument();
  });

  it("prompts a newly registered existing QQ user to bind first", async () => {
    useAuth.setState({ justRegistered: true });
    render(<App />);
    expect(await screen.findByText("已有 QQ 用户建议先绑定")).toBeInTheDocument();
    expect(screen.getByText(/继续使用原 QQ 账号的长期记忆/)).toBeInTheDocument();
  });
});
