import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, api } from "../api/client";
import { HpApi } from "../api/resources";
import { createWorkbenchStore } from "./workbench";
import { createTraceStore } from "../components/trace/traceStore";
import { useAuth } from "./auth";
import { type HpModelInputDetail, type HpTraceTree } from "../api/types";

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => {
  vi.restoreAllMocks();
});
describe("UI-1 account and selection fences", () => {
  it("aborts requests and rejects late responses without expiring the new account", async () => {
    const old = deferred<Response>();
    let signal: AbortSignal | null | undefined;
    const client = new ApiClient((_input, init) => {
      signal = init?.signal;
      return old.promise;
    });
    const expired = vi.fn();
    client.onUnauthorized = expired;
    const pending = client.request({ method: "GET", path: "/old" }).catch((e: Error) => e.name);
    client.reset();
    expect(signal?.aborted).toBe(true);
    old.resolve(json({}, 401));
    expect(await pending).toBe("AbortError");
    expect(expired).not.toHaveBeenCalled();
  });
  it("expires current 401s but leaves ordinary 403s in context", async () => {
    let status = 403;
    const client = new ApiClient(async () => json({ error: { code: "denied" } }, status));
    const expired = vi.fn();
    client.onUnauthorized = expired;
    await client.request({ method: "GET", path: "/object" }).catch(() => {});
    expect(expired).not.toHaveBeenCalled();
    status = 401;
    await client.request({ method: "GET", path: "/object" }).catch(() => {});
    expect(expired).toHaveBeenCalledOnce();
  });
  it("does not restore authentication after an expire overtakes /me", async () => {
    const old = deferred<Awaited<ReturnType<typeof api.me>>>();
    vi.spyOn(api, "me").mockReturnValue(old.promise);
    const checking = useAuth.getState().check();
    useAuth.getState().expire();
    old.resolve({ account: { account_id: "old" } } as Awaited<ReturnType<typeof api.me>>);
    await checking;
    expect(useAuth.getState().status).toBe("signedOut");
  });
  it("drops a late send after reset even when the same conversation ID is selected again", async () => {
    const result = deferred<Awaited<ReturnType<HpApi["sendMessage"]>>>();
    const fetchImpl = vi.fn();
    const store = createWorkbenchStore({
      api: { sendMessage: () => result.promise } as unknown as HpApi,
      fetchImpl,
    });
    store.setState({ activeConversationId: "c" });
    const sending = store.getState().sendMessage("private");
    store.getState().reset();
    store.setState({ activeConversationId: "c" });
    result.resolve({
      user_message: { message_id: "u" },
      assistant_message: { message_id: "a" },
      run: { run_id: "r" },
    } as Awaited<ReturnType<HpApi["sendMessage"]>>);
    expect(await sending).toBe(false);
    expect(store.getState().messages).toEqual([]);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
  it("does not continue an old upload under the next account", async () => {
    const created = deferred<Awaited<ReturnType<HpApi["createUpload"]>>>();
    const uploadContent = vi.fn();
    const deleteFile = vi.fn();
    const store = createWorkbenchStore({
      api: { createUpload: () => created.promise, uploadContent, deleteFile } as unknown as HpApi,
    });
    store.setState({ activeConversationId: "c" });
    const uploading = store.getState().addAttachments([new File(["secret"], "a.txt")]);
    store.getState().reset();
    created.resolve({ file: { file_id: "f" }, content_url: "/old" } as Awaited<
      ReturnType<HpApi["createUpload"]>
    >);
    await uploading;
    expect(uploadContent).not.toHaveBeenCalled();
    expect(deleteFile).not.toHaveBeenCalled();
    expect(store.getState().attachments).toEqual([]);
  });
  it("drops late Model Input and trace responses across reset / A-B-A", async () => {
    const input = deferred<HpModelInputDetail>();
    const tree = deferred<HpTraceTree>();
    const store = createTraceStore({
      getModelInput: () => input.promise,
      getRunTrace: () => tree.promise,
    } as unknown as HpApi);
    store.getState().followRun("a");
    const loading = store.getState().loadTrace();
    const loadingInput = store.getState().loadModelInput("private");
    store.getState().reset();
    store.getState().followRun("b");
    store.getState().followRun("a");
    input.resolve({} as HpModelInputDetail);
    tree.resolve({ run: {}, roots: [] } as unknown as HpTraceTree);
    await Promise.all([loading, loadingInput]);
    expect(store.getState().modelInputs).toEqual({});
    expect(store.getState().run).toBeNull();
  });
  it("live chat cannot steal a selected historical Trace", () => {
    const store = createTraceStore();
    store.getState().selectRun("history");
    store.getState().followRun("live");
    store.getState().applyEvent("live", {
      action: "start",
      nodeId: "live",
      parentId: null,
      name: "live",
      nodeType: "llm",
      status: null,
      metadata: {},
      durationMs: null,
      occurredAt: null,
    });
    expect(store.getState().runId).toBe("history");
    expect(store.getState().nodes).toEqual({});
  });
});
