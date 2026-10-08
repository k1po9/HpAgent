import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useTraceStore } from "../components/trace/traceStore";
import { createWorkbenchStore } from "./workbench";
import { useShell } from "./shell";
import type { HpApi } from "../api/resources";
import type { HpChatRunSnapshot } from "../api/types";
import type { RunFeedHandlers } from "../sse/runFeed";

const feed = vi.hoisted(() => ({ handlers: null as RunFeedHandlers | null }));
const originalLoadTrace = useTraceStore.getState().loadTrace;
vi.mock("../sse/runFeed", () => ({
  openRunFeed: vi.fn((_id: string, handlers: RunFeedHandlers) => {
    feed.handlers = handlers;
    return { close: vi.fn(), done: new Promise<void>(() => {}) };
  }),
}));
beforeEach(() => {
  useShell.getState().reset();
  useTraceStore.getState().reset();
  feed.handlers = null;
});
afterEach(() => {
  vi.restoreAllMocks();
  useTraceStore.setState({ loadTrace: originalLoadTrace });
  useTraceStore.getState().reset();
});
it.each([
  "ordinary",
  "overview",
  "current advanced",
  "historical advanced",
  "closed",
  "account reset",
])(
  "R3: Chat terminal refreshes only the selected current diagnostic lifecycle (%s)",
  async (mode) => {
    const run = {
      run_id: "chat-run",
      conversation_id: "c",
      status: "running",
      agent_strategy: "react",
      budget: null,
    };
    const assistant = {
      message_id: "assistant",
      conversation_id: "c",
      role: "assistant",
      status: "pending",
      content: null,
      sequence: 2,
      produced_by_run_id: "chat-run",
    };
    const terminal = {
      source_kind: "chat",
      run: { ...run, status: "succeeded" },
      assistant_message: { ...assistant, status: "completed", content: "完成" },
    } as HpChatRunSnapshot;
    const api = {
      getConversationDetail: vi.fn().mockResolvedValue({
        conversation: { conversation_id: "c" },
        active_run: { run, assistant_message: assistant },
      }),
      listMessages: vi
        .fn()
        .mockResolvedValue({ items: [assistant], next_cursor: null, has_more: false }),
      getRun: vi.fn().mockResolvedValue(terminal),
    } as unknown as HpApi;
    const trace = vi.spyOn(useTraceStore.getState(), "loadTrace").mockResolvedValue();
    const store = createWorkbenchStore({ api });
    try {
      await store.getState().selectConversation("c");
      if (mode === "overview")
        useShell.getState().openInspector({ kind: "run", objectId: "chat-run", tab: "overview" });
      if (mode === "current advanced" || mode === "closed")
        useTraceStore.getState().selectRun("chat-run");
      if (mode === "historical advanced") useTraceStore.getState().selectRun("history");
      if (mode === "closed") useTraceStore.getState().reset();
      if (mode === "account reset") store.getState().reset();
      expect(feed.handlers).not.toBeNull();
      feed.handlers!.onTerminal!(terminal);
      await Promise.resolve();
      expect(trace).toHaveBeenCalledTimes(mode === "current advanced" ? 1 : 0);
      if (mode !== "account reset") {
        expect(api.getRun).toHaveBeenCalledWith("chat-run");
        expect(store.getState().messages[0]?.content).toBe("完成");
      } else expect(store.getState().messages).toEqual([]);
      if (mode === "historical advanced") expect(useTraceStore.getState().runId).toBe("history");
    } finally {
      store.getState().reset();
    }
  },
);
