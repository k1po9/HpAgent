import { afterEach, describe, expect, it, vi } from "vitest";
import { HpApi, uploadMime } from "../api/resources";
import type { HpConversation, HpRun, HpSendResult } from "../api/types";
import { createWorkbenchStore } from "./workbench";
import { useConversationUi } from "./conversationUi";

const conversation = (id: string): HpConversation => ({
  conversation_id: id,
  title: id,
  status: "active",
  metadata_version: 1,
  last_message_seq: 0,
  created_at: "2026-10-07T00:00:00Z",
  updated_at: "2026-10-07T00:00:00Z",
});
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
const page = (ids: string[], cursor: string | null = null) => ({
  items: ids.map(conversation),
  next_cursor: cursor,
  has_more: Boolean(cursor),
});
function setup() {
  const api = {
    listConversations: vi.fn().mockResolvedValue(page([])),
    createConversation: vi.fn(),
    sendMessage: vi.fn(),
    getConversationDetail: vi
      .fn()
      .mockResolvedValue({ conversation: conversation("c1"), active_run: null }),
    listMessages: vi.fn().mockResolvedValue(page([])),
    getRun: vi.fn().mockRejectedValue(new Error("offline")),
  };
  const store = createWorkbenchStore({ api: api as unknown as HpApi });
  return { api, store };
}
afterEach(() => {
  vi.restoreAllMocks();
  useConversationUi.getState().reset();
});
describe("UI-2 query and intent recovery", () => {
  it("deduplicates overlapping conversation pages and invalidates late append on refresh", async () => {
    const { api, store } = setup();
    api.listConversations
      .mockResolvedValueOnce(page(["c1"], "cursor"))
      .mockResolvedValueOnce(page(["c1", "c2"], "next"));
    await store.getState().loadConversations();
    await store.getState().loadMoreConversations();
    expect(store.getState().conversations.map((c) => c.conversation_id)).toEqual(["c1", "c2"]);
    const late = deferred<ReturnType<typeof page>>();
    api.listConversations.mockReturnValueOnce(late.promise).mockResolvedValueOnce(page(["c3"]));
    const append = store.getState().loadMoreConversations();
    await store.getState().loadConversations();
    late.resolve(page(["old"]));
    await append;
    expect(store.getState().conversations.map((c) => c.conversation_id)).toEqual(["c3"]);
    store.getState().reset();
  });
  it("shares one create and retains the created object without taking a newer selection", async () => {
    const { api, store } = setup();
    const create = deferred<{ conversation: HpConversation }>();
    api.createConversation.mockReturnValue(create.promise);
    const one = store.getState().ensureConversation(),
      two = store.getState().ensureConversation();
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    await store.getState().selectConversation("c2");
    create.resolve({ conversation: conversation("new") });
    expect(await one).toBeNull();
    expect(await two).toBeNull();
    expect(store.getState().activeConversationId).toBe("c2");
    // Creation is retained even if the following list refresh does not contain it yet.
    expect(store.getState().conversations.some((c) => c.conversation_id === "new")).toBe(true);
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    store.getState().reset();
  });
  it("replays an unknown send with the original payload after navigating away and back", async () => {
    const { api, store } = setup();
    store.setState({ activeConversationId: "c1" });
    api.sendMessage.mockRejectedValueOnce(new TypeError("response lost"));
    expect(await store.getState().sendMessage("hello")).toBe(false);
    const first = api.sendMessage.mock.calls[0];
    expect(await store.getState().sendMessage("changed")).toBe(false);
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    await store.getState().selectConversation("c2");
    await store.getState().selectConversation("c1");
    const result = {
      user_message: { message_id: "u", content: "hello", role: "user" },
      assistant_message: { message_id: "a", role: "assistant", content: "ok" },
      run: { run_id: "r", status: "succeeded" } as HpRun,
    } as HpSendResult;
    api.sendMessage.mockResolvedValueOnce(result);
    expect(await store.getState().confirmPendingSend()).toBe(true);
    expect(api.sendMessage.mock.calls[1]).toEqual(first);
    expect(store.getState().messages.map((m) => m.message_id)).toEqual(["u", "a"]);
    expect(store.getState().pendingSendIds).toEqual([]);
    store.getState().reset();
  });
  it("invalidates creation, pagination and unknown intent on account reset", async () => {
    const { api, store } = setup();
    const create = deferred<{ conversation: HpConversation }>();
    api.createConversation.mockReturnValue(create.promise);
    const task = store.getState().ensureConversation();
    store.getState().reset();
    create.resolve({ conversation: conversation("old") });
    expect(await task).toBeNull();
    expect(store.getState().conversations).toEqual([]);
    expect(store.getState().activeConversationId).toBeNull();
  });
  it("maps known document suffixes and does not relabel unknown binary files as text", () => {
    expect(uploadMime(new File(["x"], "a.pdf"))).toBe("application/pdf");
    expect(uploadMime(new File(["x"], "a.docx"))).toContain("wordprocessingml");
    expect(uploadMime(new File(["x"], "a.bin"))).toBe("application/octet-stream");
    expect(uploadMime(new File(["x"], "a.md", { type: "text/x-markdown" }))).toBe("text/markdown");
  });
});

it("bounds empty position records without discarding non-empty drafts", () => {
  useConversationUi.getState().update("a:protected", { text: "未发草稿" });
  for (let index = 0; index < 60; index++)
    useConversationUi.getState().update(`a:${index}`, { offset: index });
  expect(useConversationUi.getState().entries["a:protected"]?.text).toBe("未发草稿");
  expect(Object.keys(useConversationUi.getState().entries).length).toBeLessThanOrEqual(32);
});
