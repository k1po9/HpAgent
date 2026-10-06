import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useAuth } from "./store/auth";
import { useWorkbench } from "./store/workbench";
import { useShell } from "./store/shell";

const conversation = (id: string) => ({
  conversation_id: id,
  title: `对话 ${id}`,
  status: "active",
  last_message_seq: 1,
  metadata_version: 1,
  created_at: "2026-10-06T00:00:00Z",
  updated_at: "2026-10-06T00:00:00Z",
});
const message = (id: string) => ({
  message_id: `${id}-message`,
  conversation_id: id,
  role: "assistant",
  status: "completed",
  content: `${id} 恢复的消息`,
  sequence: 1,
  produced_by_run_id: null,
  created_at: "2026-10-06T00:00:00Z",
  completed_at: "2026-10-06T00:00:01Z",
});
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const unavailable = () =>
  json({ error: { code: "service_unavailable", message: "临时离线", retryable: true } }, 503);
const detail = (id: string) => json({ conversation: conversation(id), active_run: null });
const messages = (id: string) =>
  json({
    items: [message(id)],
    has_more: false,
    next_cursor: null,
    conversation_last_message_seq: 1,
  });
const records = (id: string, status = "succeeded") => json({ items: [{ run_id: id, status }] });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function backend() {
  const state = {
    detail: (id: string): Response | Promise<Response> => detail(id),
    messages: (id: string): Response | Promise<Response> => messages(id),
    lookup: (_id: string): Response | Promise<Response> => records("r1"),
    detailCalls: [] as string[],
    messageCalls: [] as string[],
    lookupCalls: [] as string[],
    streamSignals: [] as AbortSignal[],
  };
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path === "/api/v1/me")
      return json({
        account: { account_id: "alice", status: "active", created_at: "2026-10-06T00:00:00Z" },
        session: {},
        csrf_token: "mock",
        identities: { web: { username: "alice" }, qq: { bound: false } },
        capabilities: {},
      });
    if (path === "/api/v1/conversations")
      return json({
        items: [conversation("c1"), conversation("c2")],
        has_more: false,
        next_cursor: null,
      });
    const selected = path.match(/^\/api\/v1\/conversations\/(c[12])$/);
    if (selected) {
      state.detailCalls.push(selected[1]!);
      return state.detail(selected[1]!);
    }
    const page = path.match(/^\/api\/v1\/conversations\/(c[12])\/messages$/);
    if (page) {
      state.messageCalls.push(page[1]!);
      return state.messages(page[1]!);
    }
    const lookup = path.match(/^\/api\/v1\/works\/([^/]+)\/runs$/);
    if (lookup) {
      state.lookupCalls.push(lookup[1]!);
      return state.lookup(lookup[1]!);
    }
    if (/^\/api\/v1\/runs\/live\/events/.test(path)) {
      state.streamSignals.push(init!.signal as AbortSignal);
      return new Response(new ReadableStream(), {
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    if (/^\/api\/v1\/runs\/[^/]+\/resources/.test(path))
      return json({ count: 0, next: null, candidates: [] });
    if (path.endsWith("/resources")) return json({ grants: [], attachments: [] });
    if (path === "/api/v1/workspace")
      return json({
        workspace_id: "space",
        root_id: "root",
        nodes: [
          {
            node_id: "root",
            parent_id: null,
            kind: "directory",
            name: "空间",
            file_id: null,
            source: null,
          },
        ],
      });
    return json({ items: [], has_more: false, next_cursor: null });
  });
  return state;
}

beforeEach(() => {
  useAuth.getState().expire();
  useWorkbench.getState().reset();
  useShell.getState().reset();
  useAuth.setState({ status: "checking" });
  window.history.replaceState(null, "", "#/ai/c1");
});
afterEach(() => {
  cleanup();
  useAuth.getState().expire();
  vi.unstubAllGlobals();
});

async function openLookup() {
  window.history.replaceState(null, "", "#/tasks");
  render(<App />);
  const summary = await screen.findByText("执行记录与诊断", { selector: "summary" });
  fireEvent.click(summary);
  return screen.getByRole("textbox", { name: "工作编号" });
}
function lookup(input: HTMLElement, id: string) {
  fireEvent.change(input, { target: { value: id } });
  fireEvent.click(screen.getByRole("button", { name: "读取执行记录" }));
}

describe("UI1-R01 conversation re-selection", () => {
  it.each(["detail", "messages"] as const)(
    "retries a failed %s load by clicking the same sidebar item",
    async (stage) => {
      const api = backend();
      api[stage] = () => unavailable();
      render(<App />);
      await screen.findByText("临时离线");
      expect(api.detailCalls).toEqual(["c1"]);
      expect(api.messageCalls).toEqual(["c1"]);
      api[stage] = stage === "detail" ? detail : messages;
      fireEvent.click(screen.getByRole("button", { name: "对话 c1" }));
      await waitFor(() => expect(api.detailCalls).toEqual(["c1", "c1"]));
      expect(api.messageCalls).toEqual(["c1", "c1"]);
      await screen.findByPlaceholderText(/输入消息/);
      expect(await screen.findByText("c1 恢复的消息")).toBeInTheDocument();
      expect(screen.queryByText("临时离线")).not.toBeInTheDocument();
    },
    10_000,
  );

  it("keeps the draft, runtime and active Run subscription when re-selecting a loaded conversation", async () => {
    const api = backend();
    api.detail = (id) =>
      json({
        conversation: conversation(id),
        active_run: {
          run: { run_id: "live", status: "running", agent_strategy: "react" },
          assistant_message: { ...message(id), status: "pending" },
        },
      });
    render(<App />);
    const composer = await screen.findByPlaceholderText(/输入消息/);
    await waitFor(() => expect(api.streamSignals).toHaveLength(1));
    fireEvent.change(composer, { target: { value: "保留草稿" } });
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "对话 c1" }));
    expect(api.detailCalls).toEqual(["c1"]);
    expect(api.messageCalls).toEqual(["c1"]);
    expect(api.streamSignals).toHaveLength(1);
    expect(api.streamSignals[0]!.aborted).toBe(false);
    expect(screen.getByPlaceholderText(/输入消息/)).toBe(composer);
    expect(composer).toHaveValue("保留草稿");
  });

  it("does not create concurrent loads when re-selecting a conversation still loading", async () => {
    const api = backend();
    const pending = deferred<Response>();
    api.detail = () => pending.promise;
    render(<App />);
    const choice = await screen.findByRole("button", { name: "对话 c1" });
    await waitFor(() => expect(api.detailCalls).toEqual(["c1"]));
    fireEvent.click(choice);
    fireEvent.click(choice);
    expect(api.detailCalls).toEqual(["c1"]);
    expect(api.messageCalls).toEqual(["c1"]);
    await act(async () => {
      pending.resolve(detail("c1"));
    });
    expect(await screen.findByText("c1 恢复的消息")).toBeInTheDocument();
  });

  it.each(["success", "failure"])(
    "ignores a late retry %s after another conversation is selected",
    async (result) => {
      const api = backend();
      api.detail = () => unavailable();
      render(<App />);
      await screen.findByText("临时离线");
      const pending = deferred<Response>();
      api.detail = (id) => (id === "c1" ? pending.promise : detail(id));
      fireEvent.click(screen.getByRole("button", { name: "对话 c1" }));
      await waitFor(() => expect(api.detailCalls).toEqual(["c1", "c1"]));
      fireEvent.click(screen.getByRole("button", { name: "对话 c2" }));
      await screen.findByText("c2 恢复的消息");
      await act(async () => {
        pending.resolve(result === "success" ? detail("c1") : unavailable());
      });
      expect(screen.getByText("c2 恢复的消息")).toBeInTheDocument();
      expect(screen.queryByText("c1 恢复的消息")).not.toBeInTheDocument();
      expect(screen.queryByText("临时离线")).not.toBeInTheDocument();
      expect(useWorkbench.getState().activeConversationId).toBe("c2");
    },
  );
});

describe("UI1-R02 work Run lookup submissions", () => {
  it("refreshes the same work ID and displays the new record", async () => {
    const api = backend();
    const input = await openLookup();
    lookup(input, "w1");
    await screen.findByRole("button", { name: "succeeded · r1" });
    api.lookup = () => records("r2", "running");
    lookup(input, "w1");
    await waitFor(() => expect(api.lookupCalls).toEqual(["w1", "w1"]));
    expect(await screen.findByRole("button", { name: "running · r2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "succeeded · r1" })).not.toBeInTheDocument();
  });

  it("recovers a failed lookup by submitting the same work ID", async () => {
    const api = backend();
    api.lookup = () => unavailable();
    const input = await openLookup();
    lookup(input, "w1");
    await screen.findByText("执行记录暂不可用。");
    api.lookup = () => records("recovered");
    lookup(input, "w1");
    await waitFor(() => expect(api.lookupCalls).toEqual(["w1", "w1"]));
    expect(
      await screen.findByRole("button", { name: "succeeded · recovered" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("执行记录暂不可用。")).not.toBeInTheDocument();
  });

  it.each(["success", "failure"])(
    "ignores a late A %s after a successful B lookup",
    async (result) => {
      const api = backend();
      const pending = deferred<Response>();
      api.lookup = (id) => (id === "a" ? pending.promise : records("b-current"));
      const input = await openLookup();
      lookup(input, "a");
      await waitFor(() => expect(api.lookupCalls).toEqual(["a"]));
      lookup(input, "b");
      await screen.findByRole("button", { name: "succeeded · b-current" });
      await act(async () => {
        pending.resolve(result === "success" ? records("a-old") : unavailable());
      });
      expect(screen.getByRole("button", { name: "succeeded · b-current" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "succeeded · a-old" })).not.toBeInTheDocument();
      expect(screen.queryByText("执行记录暂不可用。")).not.toBeInTheDocument();
    },
  );

  it("keeps the newer same-ID lookup when the earlier response arrives last", async () => {
    const api = backend();
    const pending = deferred<Response>();
    api.lookup = () => pending.promise;
    const input = await openLookup();
    lookup(input, "w1");
    await waitFor(() => expect(api.lookupCalls).toEqual(["w1"]));
    api.lookup = () => records("newer");
    lookup(input, "w1");
    await waitFor(() => expect(api.lookupCalls).toEqual(["w1", "w1"]));
    await screen.findByRole("button", { name: "succeeded · newer" });
    await act(async () => {
      pending.resolve(records("older"));
    });
    expect(screen.getByRole("button", { name: "succeeded · newer" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "succeeded · older" })).not.toBeInTheDocument();
  });

  it("keeps the current B error when an older A lookup succeeds late", async () => {
    const api = backend();
    const pending = deferred<Response>();
    api.lookup = (id) => (id === "a" ? pending.promise : unavailable());
    const input = await openLookup();
    lookup(input, "a");
    await waitFor(() => expect(api.lookupCalls).toEqual(["a"]));
    lookup(input, "b");
    await screen.findByText("执行记录暂不可用。");
    await act(async () => {
      pending.resolve(records("a-old"));
    });
    expect(screen.getByText("执行记录暂不可用。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "succeeded · a-old" })).not.toBeInTheDocument();
  });

  it("rejects whitespace-only work IDs without dispatching a request", async () => {
    const api = backend();
    const input = await openLookup();
    lookup(input, "   ");
    expect(screen.getByText("请输入工作编号。")).toBeInTheDocument();
    expect(api.lookupCalls).toEqual([]);
  });

  it("does not restore lookup results or an Inspector after expiry overtakes the response", async () => {
    const api = backend();
    const pending = deferred<Response>();
    api.lookup = () => pending.promise;
    const input = await openLookup();
    lookup(input, "w1");
    await waitFor(() => expect(api.lookupCalls).toEqual(["w1"]));
    act(() => useAuth.getState().expire());
    await act(async () => {
      pending.resolve(records("private-old"));
    });
    expect(screen.getByRole("heading", { name: "HpAgent 登录" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText(/private-old/)).not.toBeInTheDocument();
    expect(useShell.getState().route.inspector).toBeUndefined();
  });
});
