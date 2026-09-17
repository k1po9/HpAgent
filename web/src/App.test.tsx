/**
 * Workbench render smoke test.
 *
 * Mounts the whole auth-gated app against a stubbed fetch: a signed-in /me,
 * one conversation, and an empty message page. Verifies the assistant-ui chat
 * surface renders (sidebar + composer) without runtime errors.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useAuth } from "./store/auth";
import { useArtifacts } from "./store/artifacts";

const ME = {
  account: { account_id: "alice", status: "active", created_at: "2026-08-08T00:00:00Z" },
  session: { expires_at: "2026-08-09T00:00:00Z", idle_expires_at: "2026-08-08T01:00:00Z" },
  csrf_token: "t",
  identities: { web: { username: "alice" }, qq: { bound: false } },
  capabilities: {},
};

let capabilities: Record<string, unknown> = {};

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
  capabilities = {};
  useAuth.setState({
    status: "checking",
    account: null,
    identities: null,
    justRegistered: false,
  });
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/me") return json({ ...ME, capabilities });
    if (url === "/api/v1/identity-bindings/qq/challenges") {
      return json({
        challenge_id: "challenge-1",
        code: "HP-123456",
        status: "pending",
        expires_at: "2026-08-16T00:05:00Z",
      });
    }
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

  it("shows Artifact store errors in the active chat", async () => {
    render(<App />);
    await screen.findByPlaceholderText(/输入消息/);

    useArtifacts.setState({ error: "Artifact 服务不可用" });

    await waitFor(() =>
      expect(screen.getByText("Artifact：Artifact 服务不可用（点击关闭）")).toBeInTheDocument(),
    );
  });

  it("prompts a newly registered existing QQ user to bind first", async () => {
    const user = userEvent.setup();
    useAuth.setState({ justRegistered: true });
    render(<App />);
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("已有 QQ 用户建议先绑定")).toBeInTheDocument();
    expect(screen.getByText(/继续使用原 QQ 账号的长期记忆/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "绑定已有 QQ" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "以后再说" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "绑定已有 QQ" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(await screen.findByText("绑定 HP-123456")).toBeInTheDocument();
  });

  it("uploads and renders an attachment when the server enables file upload", async () => {
    capabilities = { file_upload: true };
    const baseFetch = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url === "/api/v1/conversations/c1/uploads") {
        return json({
          file: {
            file_id: "f1",
            file_name: "notes.txt",
            purpose: "input",
            status: "uploading",
            size_bytes: 5,
            content_type: "text/plain",
            encoding: null,
            sha256: null,
            failure_code: null,
            download_url: null,
          },
          content_url: "/api/v1/uploads/f1/content",
        });
      }
      if (init?.method === "PUT" && url === "/api/v1/uploads/f1/content") {
        return json({
          file: {
            file_id: "f1",
            file_name: "notes.txt",
            purpose: "input",
            status: "ready",
            size_bytes: 5,
            content_type: "text/plain",
            encoding: "utf-8",
            sha256: "a".repeat(64),
            failure_code: null,
            download_url: "/api/v1/files/f1/content",
          },
        });
      }
      return baseFetch(input, init);
    });
    render(<App />);
    await screen.findByPlaceholderText(/输入消息/);

    const input = document.querySelector<HTMLInputElement>(
      '.hp-composer__attach input[type="file"]',
    );
    expect(input).not.toBeNull();
    fireEvent.change(input as HTMLInputElement, {
      target: { files: [new File(["hello"], "notes.txt", { type: "text/plain" })] },
    });

    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
    expect(await screen.findByText("已就绪")).toBeInTheDocument();
  });
});
