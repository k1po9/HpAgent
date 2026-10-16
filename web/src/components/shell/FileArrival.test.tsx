import { act, fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InspectorHost } from "./InspectorHost";
import { useShell } from "../../store/shell";
import { useWorkbench } from "../../store/workbench";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { playFilament } from "../spatial/filament";
import { setMotionPreference } from "../motion/preference";
import type { HpFile, HpWorkspaceNode } from "../../api/types";
vi.mock("../spatial/filament", () => ({ playFilament: vi.fn(() => vi.fn()) }));
const flights = vi.mocked(playFilament);
const dir: HpWorkspaceNode = {
  node_id: "dir",
  parent_id: null,
  name: "Documents",
  kind: "directory",
  file_id: null,
  revision: null,
  destination_id: null,
  source: null,
};
const node = (id: string): HpWorkspaceNode => ({
  ...dir,
  node_id: id,
  parent_id: "dir",
  name: `${id}.txt`,
  kind: "file",
  file_id: id,
  revision: 1,
});
const file = (id: string): HpFile => ({
  file_id: id,
  file_name: `${id}.txt`,
  purpose: "input",
  status: "ready",
  content_type: "text/plain",
  encoding: "utf-8",
  size_bytes: 20,
  sha256: id,
  failure_code: null,
  download_url: null,
});
let source: HTMLButtonElement;
let pending: Map<string, (value: { file: HpFile }) => void>;
beforeEach(() => {
  useShell.getState().reset();
  useWorkbench.getState().reset();
  useWorkspace.getState().reset();
  setMotionPreference("system");
  flights.mockClear();
  pending = new Map();
  source = document.createElement("button");
  source.id = "file-origin";
  document.body.append(source);
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "dir",
    nodes: [dir, node("a"), node("b"), node("c")],
  });
  vi.spyOn(workspaceApi, "getFile").mockImplementation(
    (id) => new Promise((done) => pending.set(id, done)),
  );
  vi.spyOn(workspaceApi, "readFileText").mockImplementation(async (id) => `body ${id}`);
});
afterEach(() => {
  cleanup();
  source.remove();
  vi.restoreAllMocks();
});
function setup(compact = false) {
  return render(<InspectorHost compact={compact} onSaveHtml={vi.fn()} />);
}
function open(id: string, child = false) {
  useShell.getState().openInspector(
    {
      kind: "file",
      objectId: id,
      origin: { triggerId: source.id, conversationId: "conversation", runId: "run" },
    },
    child,
  );
}
async function loaded(id: string) {
  await waitFor(() => expect(pending.has(id)).toBe(true));
  await act(async () => pending.get(id)!({ file: file(id) }));
}
function land() {
  act(() => {
    const f = flights.mock.calls.at(-1)![0];
    f.arrive(true);
    f.settle();
  });
}
it("starts tree, metadata and preview before actual arrival; holds tabs and content", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  await waitFor(() => expect(workspaceApi.readFileText).toHaveBeenCalled());
  expect(flights).toHaveBeenCalledTimes(1);
  expect(flights.mock.calls[0]![0].target.id).toBe("inspector-title");
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "接纳中");
  expect(screen.queryByRole("tab")).toBeNull();
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("inert");
  land();
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "文件详情");
  expect(screen.getByText("body a")).toBeVisible();
  expect(useShell.getState().route.inspector?.origin).toMatchObject({
    runId: "run",
    conversationId: "conversation",
  });
});
it("arrival before metadata shows only this file's loading state", async () => {
  setup();
  act(() => open("b"));
  land();
  await screen.findByText("正在读取文件信息…");
  expect(screen.queryByText("body a")).toBeNull();
  await loaded("b");
  await screen.findByText("body b");
});
it("late metadata and stale arrival cannot reveal a newer target", async () => {
  setup();
  act(() => open("a"));
  await waitFor(() => expect(pending.has("a")).toBe(true));
  const old = flights.mock.calls.at(-1)![0];
  act(() => open("b"));
  await loaded("a");
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  expect(useShell.getState().route.inspector?.objectId).toBe("b");
  await loaded("b");
  land();
  await screen.findByText("body b");
  expect(screen.queryByText("body a")).toBeNull();
});
it("keeps preview DOM through tabs, expansion and repeated open", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  const preview = await screen.findByText("body a");
  act(() => useShell.getState().setInspectorTab("usage"));
  expect(preview).not.toBeVisible();
  act(() => useShell.getState().setInspectorTab("preview"));
  act(() => useShell.setState({ expanded: true }));
  act(() => open("a"));
  expect(screen.getByText("body a")).toBe(preview);
  expect(workspaceApi.readFileText).toHaveBeenCalledTimes(1);
  expect(flights).toHaveBeenCalledTimes(1);
});
it("version updates replace preview only; old content response cannot win", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  const preview = await screen.findByText("body a");
  act(() =>
    useWorkspace.setState((s) => ({
      tree: { ...s.tree!, nodes: [dir, { ...node("a"), file_id: "a-v2", revision: 2 }] },
    })),
  );
  await loaded("a-v2");
  await screen.findByText("body a-v2");
  expect(preview.isConnected).toBe(false);
  expect(flights).toHaveBeenCalledTimes(1);
});
it("permission failure settles immediately and removes preview and actions", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  const old = flights.mock.calls.at(-1)![0];
  act(() =>
    useWorkspace.setState((s) => ({
      cache: { ...s.cache, "file:a": { loading: false, error: "Forbidden" } },
    })),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("文件信息不可用");
  expect(screen.queryByRole("link", { name: "下载" })).toBeNull();
  expect(screen.queryByText("body a")).toBeNull();
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
});
it("dirty authorization prevents tabs, close and child navigation from changing history", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  const route = useShell.getState().route;
  act(() => useShell.setState({ dirtyResourceEditor: "grant-draft" }));
  act(() => useShell.getState().setInspectorTab("details"));
  expect(useShell.getState().route).toBe(route);
  act(() => useShell.setState({ pendingRoute: null }));
  act(() => useShell.getState().closeInspector());
  expect(useShell.getState().route).toBe(route);
  expect(document.querySelector(".hp-inspector-exit")).toBeNull();
  act(() => open("b", true));
  expect(useShell.getState().backStack).toEqual([]);
  act(() => {
    const next = useShell.getState().pendingResourceChange!;
    useShell.setState({ dirtyResourceEditor: null, pendingResourceChange: null });
    next();
  });
  expect(useShell.getState().backStack).toEqual([route.inspector]);
});
it("X leaves an inert empty shell, and late callbacks cannot hide the reopened target", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  fireEvent.click(screen.getByRole("button", { name: "关闭文件详情" }));
  expect(useShell.getState().route.inspector).toBeUndefined();
  const shell = document.querySelector(".hp-inspector-exit");
  expect(shell).toHaveAttribute("inert");
  expect(shell?.textContent).toBe("");
  expect(shell?.querySelector("button,iframe,input,a")).toBeNull();
  const old = flights.mock.calls.at(-1)![0];
  act(() => open("b"));
  await loaded("b");
  land();
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(document.querySelector(".hp-inspector-exit")).toBeNull();
  expect(screen.getByText("body b")).toBeVisible();
});
it("directory uses the same gate and never calls file APIs; Back restores its parent", async () => {
  setup();
  act(() => open("dir"));
  await screen.findByText("目录：Documents");
  expect(workspaceApi.getFile).not.toHaveBeenCalled();
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  land();
  const parent = useShell.getState().route.inspector;
  act(() => open("a", true));
  await loaded("a");
  land();
  act(() => useShell.getState().back());
  expect(useShell.getState().route.inspector).toEqual(parent);
  expect(screen.getByText("目录：Documents")).toBeVisible();
  expect(flights).toHaveBeenCalledTimes(2);
});
it.each(["no-source", "compact", "reduced"])("%s safely reveals without a flight", async (mode) => {
  if (mode === "reduced") setMotionPreference("reduce");
  setup(mode === "compact");
  act(() => {
    if (mode === "no-source") {
      source.focus();
      useShell.getState().openInspector({ kind: "file", objectId: "a" });
    } else open("a");
  });
  await loaded("a");
  expect(flights).not.toHaveBeenCalled();
  expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
});
