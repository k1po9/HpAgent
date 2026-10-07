import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkspace, workspaceApi } from "./workspace";
import type { HpWorkspace, HpWorkspaceSearchPage } from "../api/types";
const tree: HpWorkspace = { workspace_id: "w", root_id: "root", nodes: [] };
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { resolve, promise };
}
beforeEach(() => {
  vi.restoreAllMocks();
  useWorkspace.getState().reset();
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue(tree);
});
describe("Workspace query ownership", () => {
  it("shares one tree request and fences replaced requests even for the same object", async () => {
    const old = deferred<HpWorkspace>();
    const read = vi.mocked(workspaceApi.getWorkspace).mockReturnValueOnce(old.promise);
    const a = useWorkspace.getState().loadTree();
    const b = useWorkspace.getState().loadTree();
    await Promise.resolve();
    expect(read).toHaveBeenCalledTimes(1);
    await useWorkspace.getState().loadTree(true);
    old.resolve({ ...tree, workspace_id: "stale" });
    await Promise.allSettled([a, b]);
    expect(useWorkspace.getState().tree?.workspace_id).toBe("w");
  });
  it("does not restore any cache after an account reset", async () => {
    const old = deferred<string>();
    const pending = useWorkspace.getState().query("file:A", () => old.promise);
    useWorkspace.getState().reset();
    old.resolve("account A body");
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(useWorkspace.getState().cache).toEqual({});
  });
  it("retries a failed query and reuses a successful cache", async () => {
    const read = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue("ready");
    await expect(useWorkspace.getState().query("x", read)).rejects.toThrow("offline");
    expect(await useWorkspace.getState().query("x", read)).toBe("ready");
    expect(await useWorkspace.getState().query("x", read)).toBe("ready");
    expect(read).toHaveBeenCalledTimes(2);
  });
});
function page(id: string, next: string | null = null): HpWorkspaceSearchPage {
  return {
    items: [
      {
        node_id: id,
        name: id,
        file_id: id,
        content_type: null,
        size_bytes: null,
        purpose: "input",
        source_run_id: null,
        source_work_id: null,
        revision: null,
      },
    ],
    next_after: next,
  };
}
describe("Workspace search", () => {
  it("does not mix a late old page with a newly applied filter", async () => {
    const old = deferred<HpWorkspaceSearchPage>();
    const search = vi
      .spyOn(workspaceApi, "searchWorkspace")
      .mockResolvedValueOnce(page("a", "cursor"))
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(page("b"));
    await useWorkspace.getState().searchFiles({ name: "a" });
    const pending = useWorkspace.getState().searchFiles(undefined, true);
    await useWorkspace.getState().searchFiles({ name: "b" });
    old.resolve(page("late-a"));
    await pending;
    expect(useWorkspace.getState().search?.items.map((i) => i.node_id)).toEqual(["b"]);
    expect(search.mock.calls[1]).toEqual([{ name: "a" }, "cursor"]);
  });
  it("retains existing results after a failed page and deduplicates on retry", async () => {
    vi.spyOn(workspaceApi, "searchWorkspace")
      .mockResolvedValueOnce(page("a", "cursor"))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ items: [...page("a").items, ...page("b").items], next_after: null });
    await useWorkspace.getState().searchFiles({ name: "x" });
    await useWorkspace.getState().searchFiles(undefined, true);
    expect(useWorkspace.getState().search?.items).toHaveLength(1);
    await useWorkspace.getState().searchFiles(undefined, true);
    expect(useWorkspace.getState().search?.items).toHaveLength(2);
  });
  it("locks duplicate load-more requests and marks mutation results stale", async () => {
    const pending = deferred<HpWorkspaceSearchPage>();
    const search = vi
      .spyOn(workspaceApi, "searchWorkspace")
      .mockResolvedValueOnce(page("a", "cursor"))
      .mockReturnValueOnce(pending.promise);
    await useWorkspace.getState().searchFiles({});
    const more = useWorkspace.getState().searchFiles(undefined, true);
    await useWorkspace.getState().searchFiles(undefined, true);
    expect(search).toHaveBeenCalledTimes(2);
    useWorkspace.getState().invalidate();
    pending.resolve(page("late"));
    await more;
    expect(useWorkspace.getState().searchStale).toBe(true);
    expect(useWorkspace.getState().search?.items[0]?.node_id).toBe("a");
  });
});
