/**
 * Workbench render smoke test.
 *
 * Mounts the whole auth-gated app against a stubbed fetch: a signed-in /me,
 * one conversation, and an empty message page. Verifies the assistant-ui chat
 * surface renders (sidebar + composer) without runtime errors.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useAuth } from "./store/auth";
import { useArtifacts } from "./store/artifacts";
import { useShell } from "./store/shell";
import { StrictMode } from "react";
import { useWorkbench } from "./store/workbench";

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
  window.history.replaceState(null, "", "/#/ai/c1");
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/me") return json({ ...ME, capabilities });
    if (url === "/api/v1/conversations/c1/resources") return json({ grants: [], attachments: [] });
    if (/^\/api\/v1\/runs\/[^/]+\/resources/.test(url))
      return json({ count: 0, next: null, candidates: [] });
    if (url === "/api/v1/workspace")
      return json({
        workspace_id: "w1",
        root_id: "root",
        nodes: [
          {
            node_id: "root",
            parent_id: null,
            kind: "directory",
            name: "",
            file_id: null,
            source: null,
          },
        ],
      });
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
  it("shows a failed creation before any conversation is selected and clears it on success", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/#/ai");
    const baseFetch = globalThis.fetch;
    let fail = true;
    useWorkbench.setState({
      activeConversationId: null,
      activeRun: null,
      conversations: [],
      conversationsLoaded: false,
      error: null,
    });
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      if (
        String(input).startsWith("/api/v1/conversations?") ||
        String(input) === "/api/v1/conversations"
      ) {
        if (init?.method === "POST") {
          if (fail)
            return new Response(
              JSON.stringify({
                error: {
                  code: "csrf_failed",
                  message: "请求来源不匹配",
                  request_id: "req",
                  retryable: false,
                  details: {},
                },
              }),
              { status: 403, headers: { "Content-Type": "application/json" } },
            );
          return json({ conversation: CONVERSATION });
        }
        return json({ items: [], has_more: false, next_cursor: null });
      }
      if (String(input) === "/api/v1/conversations/c1/messages" && init?.method === "POST")
        return new Response(
          JSON.stringify({
            error: {
              code: "invalid_content",
              message: "测试校验拒绝",
              retryable: false,
              request_id: "req",
              details: {},
            },
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        );
      return baseFetch(input, init);
    });
    render(<App />);
    const composer = await screen.findByPlaceholderText(/输入消息/);
    fireEvent.change(composer, { target: { value: "第一次提交" } });
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/请求来源不匹配/)).toBeInTheDocument();
    expect(composer).toHaveValue("第一次提交");
    fireEvent.click(screen.getByText(/请求来源不匹配/));
    expect(screen.queryByText(/请求来源不匹配/)).not.toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(useWorkbench.getState().activeConversationId).toBe("c1"));
    expect(screen.queryByText(/请求来源不匹配/)).not.toBeInTheDocument();
  }, 10_000);
  it("shows a retry when the API session probe fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接 HpAgent API");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("renders the conversation list and the composer after signing in", async () => {
    render(<App />);
    expect(await screen.findByRole("button", { name: "测试对话" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument();
    expect(screen.getByText("新建", { selector: "button" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "账户设置" }));
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

describe("UI-1 Shell lifecycle", () => {
  it("isolates a late account A list after switching A-B-A", async () => {
    const base = globalThis.fetch;
    let finish!: (response: Response) => void;
    let first = true;
    let title = "当前 A 的对话";
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/conversations" || url.startsWith("/api/v1/conversations?")) {
        if (first) {
          first = false;
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        }
        return json({ items: [{ ...CONVERSATION, title }], has_more: false, next_cursor: null });
      }
      return base(input, init);
    });
    render(<App />);
    await screen.findByPlaceholderText(/输入消息/);
    title = "B 的对话";
    act(() => useAuth.setState({ account: { ...ME.account, account_id: "bob" } }));
    await screen.findByRole("button", { name: "B 的对话" });
    title = "当前 A 的对话";
    act(() => useAuth.setState({ account: ME.account }));
    await screen.findByRole("button", { name: "当前 A 的对话" });
    await act(async () => {
      finish(
        json({
          items: [{ ...CONVERSATION, title: "旧 A 的私有数据" }],
          has_more: false,
          next_cursor: null,
        }),
      );
    });
    expect(screen.queryByText("旧 A 的私有数据")).not.toBeInTheDocument();
    expect(screen.queryByText("B 的对话")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "当前 A 的对话" })).toBeInTheDocument();
    expect(useShell.getState().route.inspector).toBeUndefined();
  });

  it("retains the active Run subscription across pages and aborts it on expiry", async () => {
    const base = globalThis.fetch;
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/conversations/c1") {
        return json({
          conversation: CONVERSATION,
          active_run: {
            run: { run_id: "live", status: "running", agent_strategy: "react" },
            assistant_message: {
              message_id: "pending",
              role: "assistant",
              content: "",
              status: "pending",
            },
          },
        });
      }
      if (url.includes("/runs/live/events")) {
        signals.push(init!.signal as AbortSignal);
        return new Response(new ReadableStream(), {
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return base(input, init);
    });
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    await screen.findByPlaceholderText(/输入消息/);
    await waitFor(() => expect(signals).toHaveLength(1));
    const nav = screen.getByRole("navigation", { name: "主导航" });
    for (const name of ["空间", "任务", "AI"]) {
      fireEvent.click(within(nav).getByRole("button", { name }));
    }
    act(() => useShell.getState().openInspector({ kind: "file", objectId: "root" }));
    await screen.findByRole("heading", { name: "目录：根目录" });
    fireEvent.click(screen.getByRole("button", { name: "关闭目录详情" }));
    expect(signals).toHaveLength(1);
    expect(signals[0]!.aborted).toBe(false);
    act(() => useAuth.getState().expire());
    expect(signals[0]!.aborted).toBe(true);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("never renders business content before /me succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("正在恢复会话");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });
  it("keeps one chat runtime and its draft across navigation and an unavailable Inspector", async () => {
    const base = globalThis.fetch;
    const requests: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(url);
      if (url === "/api/v1/runs/missing")
        return new Response(JSON.stringify({ error: { code: "not_found" } }), { status: 404 });
      return base(input, init);
    });
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    const composer = await screen.findByPlaceholderText(/输入消息/);
    fireEvent.change(composer, { target: { value: "尚未发送的草稿" } });
    const details = requests.filter((url) => url === "/api/v1/conversations/c1").length;
    const nav = screen.getByRole("navigation", { name: "主导航" });
    expect(within(nav).getAllByRole("button")).toHaveLength(3);
    fireEvent.click(within(nav).getByRole("button", { name: "空间" }));
    fireEvent.click(within(nav).getByRole("button", { name: "任务" }));
    fireEvent.click(within(nav).getByRole("button", { name: "AI" }));
    act(() => useShell.getState().openInspector({ kind: "run", objectId: "missing" }));
    expect(await screen.findByText("对象不可用。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "回到所属页面" }));
    expect(screen.getByPlaceholderText(/输入消息/)).toBe(composer);
    expect(composer).toHaveValue("尚未发送的草稿");
    expect(requests.filter((url) => url === "/api/v1/conversations/c1")).toHaveLength(details);
    expect(useShell.getState().route.screen).toBe("ai");
  });
  it("clears session projections synchronously on account expiry", async () => {
    render(<App />);
    await screen.findByPlaceholderText(/输入消息/);
    act(() => {
      useShell.getState().openInspector({ kind: "file", objectId: "old" });
      useAuth.getState().expire();
    });
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(useWorkbench.getState().messages).toEqual([]);
    expect(useWorkbench.getState().activeConversationId).toBeNull();
    expect(useShell.getState().route.inspector).toBeUndefined();
    expect(useArtifacts.getState().versionsByArtifactId).toEqual({});
  });
});
