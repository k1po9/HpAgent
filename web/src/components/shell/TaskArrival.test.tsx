import { Theme } from "@radix-ui/themes";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InspectorHost } from "./InspectorHost";
import { api } from "../../api/client";
import { HpCommandError, type HpWork } from "../../api/types";
import { useShell } from "../../store/shell";
import { useWorks } from "../../store/works";
import { useWorkspace } from "../../store/workspace";
import { useWorkbench } from "../../store/workbench";
import { workFixture } from "../tasks/taskFixtures";
import { resetTaskQueries } from "../tasks/useTaskQuery";
import { TaskRow } from "../tasks/TaskRow";
import { playFilament } from "../spatial/filament";
import { setMotionPreference } from "../motion/preference";
vi.mock("../spatial/filament", () => ({ playFilament: vi.fn(() => vi.fn()) }));
const flights = vi.mocked(playFilament);
let source: HTMLButtonElement;
let pending: Map<
  string,
  { resolve: (value: { work: HpWork }) => void; reject: (e: Error) => void }
>;
const work = (id: string) => workFixture({ work_id: id, title: `Task ${id}` });
beforeEach(() => {
  useShell.getState().reset();
  useWorks.getState().reset();
  useWorkspace.getState().reset();
  useWorkbench.getState().reset();
  resetTaskQueries();
  setMotionPreference("system");
  flights.mockClear();
  pending = new Map();
  source = document.createElement("button");
  source.id = "task-origin";
  document.body.append(source);
  vi.spyOn(api, "request").mockImplementation(({ path }) => {
    const id = path.match(/^\/api\/v1\/works\/([^/]+)$/)?.[1];
    if (id) return new Promise((resolve, reject) => pending.set(id, { resolve, reject })) as never;
    if (path === "/api/v1/workspace")
      return Promise.resolve({ root_id: "root", nodes: [] }) as never;
    return Promise.resolve({ items: [], grants: [], events: [] }) as never;
  });
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
      kind: "task",
      objectId: id,
      origin: { triggerId: source.id, workId: id, sourceRequirementRevision: 2 },
    },
    child,
  );
}
async function loaded(id: string) {
  await waitFor(() => expect(pending.has(id)).toBe(true));
  await act(async () => pending.get(id)!.resolve({ work: work(id) }));
}
function land() {
  act(() => {
    const f = flights.mock.calls.at(-1)![0];
    f.arrive(true);
    f.settle();
  });
}
it("starts Task refresh under neutral hidden/inert gate before the actual arrival", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  expect(flights).toHaveBeenCalledTimes(1);
  expect(flights.mock.calls[0]![0].target.id).toBe("inspector-title");
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "接纳中");
  expect(screen.queryByRole("tab")).toBeNull();
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("inert");
  land();
  expect(screen.getByRole("heading", { name: "Task a" })).toBeVisible();
});
it("arrival before response shows only current Task loading", async () => {
  setup();
  act(() => open("a"));
  land();
  expect(screen.getByRole("status")).toHaveTextContent("正在加载任务");
  await loaded("a");
  expect(screen.getByRole("heading", { name: "Task a" })).toBeVisible();
});
it("late A response and arrival cannot reveal C after A→B→C", async () => {
  setup();
  act(() => open("a"));
  const old = flights.mock.calls.at(-1)![0];
  act(() => open("b"));
  act(() => open("c"));
  await loaded("a");
  await loaded("b");
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  await loaded("c");
  land();
  expect(screen.getByRole("heading", { name: "Task c" })).toBeVisible();
  expect(screen.queryByText("Task a")).toBeNull();
});
it.each([403, 404])(
  "%s removes cached forms and safely settles without waiting",
  async (status) => {
    useWorks.getState().upsert(work("a"));
    setup();
    act(() => open("a"));
    await waitFor(() => expect(pending.has("a")).toBe(true));
    await act(async () =>
      pending.get("a")!.reject(
        new HpCommandError(status, {
          code: "unavailable",
          message: "denied",
          request_id: "test",
          retryable: false,
          details: {},
        }),
      ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("对象不可用");
    expect(screen.queryByRole("tab")).toBeNull();
    expect(document.querySelector(".hp-task-inspector")).toBeNull();
    expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
  },
);
it("retains advanced form DOM, values and query owners across tabs/expansion/repeated open", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  act(() => useShell.getState().setInspectorTab("advanced"));
  const field = screen.getByLabelText("已验证来源消息编号");
  fireEvent.change(field, { target: { value: "verified-message" } });
  const reads = vi.mocked(api.request).mock.calls.length;
  act(() => useShell.getState().setInspectorTab("overview"));
  expect(field).not.toBeVisible();
  expect(field.closest("[role=tabpanel]")).toHaveAttribute("inert");
  act(() => useShell.setState({ expanded: true }));
  act(() => useShell.getState().setInspectorTab("advanced"));
  act(() => useShell.getState().openInspector(useShell.getState().route.inspector!));
  expect(screen.getByLabelText("已验证来源消息编号")).toBe(field);
  expect(field).toHaveValue("verified-message");
  expect(vi.mocked(api.request).mock.calls.length).toBe(reads);
  expect(flights).toHaveBeenCalledTimes(1);
});
it("dirty Task blocks tabs, close and children; confirmed navigation releases retained editor", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  fireEvent.click(screen.getByRole("button", { name: "修改要求" }));
  await loaded("a");
  const field = await screen.findByLabelText("目标 / 提醒内容");
  fireEvent.change(field, { target: { value: "private draft" } });
  const route = useShell.getState().route;
  act(() => useShell.getState().setInspectorTab("outputs"));
  expect(useShell.getState().route).toBe(route);
  act(() => useShell.setState({ pendingRoute: null }));
  expect(screen.getByLabelText("目标 / 提醒内容")).toBe(field);
  expect(field).toHaveValue("private draft");
  act(() => useShell.getState().closeInspector());
  expect(useShell.getState().route).toBe(route);
  act(() => open("b", true));
  expect(useShell.getState().backStack).toEqual([]);
  act(() => useShell.setState({ pendingRoute: null }));
  act(() => useShell.getState().setInspectorTab("advanced"));
  act(() => {
    const next = useShell.getState().pendingRoute!;
    useShell.setState({ dirtyTaskEditor: null });
    useShell.getState().navigate(next);
  });
  expect(field.isConnected).toBe(false);
  act(() => useShell.getState().setInspectorTab("overview"));
  expect(screen.queryByLabelText("目标 / 提醒内容")).toBeNull();
  expect(flights).toHaveBeenCalledTimes(1);
});
it("X clears business DOM immediately; old close callbacks cannot hide a reopened Task", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  fireEvent.click(screen.getByRole("button", { name: "关闭任务详情" }));
  expect(useShell.getState().route.inspector).toBeUndefined();
  const shell = document.querySelector(".hp-inspector-exit");
  expect(shell).toHaveAttribute("inert");
  expect(shell?.textContent).toBe("");
  expect(shell?.querySelector("button,input,textarea,iframe,a")).toBeNull();
  const old = flights.mock.calls.at(-1)![0];
  act(() => open("b"));
  await loaded("b");
  land();
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(screen.getByRole("heading", { name: "Task b" })).toBeVisible();
});
it("Back restores parent tab and origin without a new flight", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  act(() => useShell.getState().setInspectorTab("advanced"));
  const parent = useShell.getState().route.inspector;
  act(() => open("b", true));
  await loaded("b");
  land();
  act(() => useShell.getState().back());
  await loaded("a");
  expect(useShell.getState().route.inspector).toEqual(parent);
  expect(flights).toHaveBeenCalledTimes(2);
});
it("binds the real TaskRow title source and restores it on close", async () => {
  const a = work("a");
  render(
    <Theme>
      <TaskRow work={a} />
    </Theme>,
  );
  setup();
  const trigger = screen.getByRole("button", { name: "Task a" });
  fireEvent.click(trigger);
  await loaded("a");
  land();
  expect(flights.mock.calls[0]![0].source).toBe(trigger);
  act(() => useShell.getState().closeInspector());
  await act(async () => {});
  expect(document.activeElement).toBe(trigger);
});
it.each(["no-source", "compact", "reduced"])(
  "%s safely shows Task without fabricated source",
  async (mode) => {
    if (mode === "reduced") setMotionPreference("reduce");
    setup(mode === "compact");
    act(() => {
      source.focus();
      if (mode === "no-source") useShell.getState().openInspector({ kind: "task", objectId: "a" });
      else open("a");
    });
    await loaded("a");
    expect(flights).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Task a" })).toBeVisible();
  },
);
