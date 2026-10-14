import { render, act, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InspectorHost } from "./InspectorHost";
import { useAuth } from "../../store/auth";
import { useShell } from "../../store/shell";
import { useWorkbench } from "../../store/workbench";
import { useRunInspector, runApi } from "../../store/runInspector";
import { setMotionPreference } from "../motion/preference";
import { playFilament } from "../spatial/filament";
vi.mock("../spatial/filament", () => ({ playFilament: vi.fn(() => vi.fn()) }));
const flights = vi.mocked(playFilament);
let source: HTMLButtonElement;
const nativeAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");
function titleAnimations() {
  const entries: { node: HTMLElement; finish: () => void }[] = [];
  Object.defineProperty(HTMLElement.prototype, "animate", {
    configurable: true,
    value: function (this: HTMLElement) {
      let finish!: () => void;
      const finished = new Promise<void>((resolve) => {
        finish = resolve;
      });
      entries.push({ node: this, finish });
      return { finished, cancel: vi.fn() };
    },
  });
  return entries;
}
async function finishTitle(entries: ReturnType<typeof titleAnimations>, index = 0) {
  await act(async () => {
    entries.filter((e) => e.node.id === "inspector-run-title")[index]!.finish();
    await Promise.resolve();
  });
}
beforeEach(() => {
  useShell.getState().reset();
  useWorkbench.getState().reset();
  useRunInspector.getState().reset();
  setMotionPreference("system");
  flights.mockClear();
  source = document.createElement("button");
  source.id = "run-source";
  document.body.append(source);
  vi.spyOn(runApi, "getRun").mockImplementation(() => new Promise(() => {}));
});
afterEach(() => {
  cleanup();
  source.remove();
  vi.restoreAllMocks();
  if (nativeAnimate) Object.defineProperty(HTMLElement.prototype, "animate", nativeAnimate);
  else delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate;
});
const open = (id: string) =>
  useShell
    .getState()
    .openInspector({ kind: "run", objectId: id, origin: { triggerId: source.id } });
it("queries before arrival, gates every business control, rejects old arrival and keeps Tab query lifetime", () => {
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  expect(runApi.getRun).toHaveBeenCalledWith("A", expect.any(AbortSignal));
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "接纳中");
  expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("inert");
  const a = flights.mock.calls.at(-1)![0];
  act(() => open("B"));
  const b = flights.mock.calls.at(-1)![0];
  act(() => {
    a.arrive();
    a.settle();
  });
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "接纳中");
  act(() => b.arrive());
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "执行详情");
  const count = flights.mock.calls.length,
    reads = vi.mocked(runApi.getRun).mock.calls.length;
  act(() => {
    useShell.getState().setInspectorTab("advanced");
    useShell.setState({ expanded: true });
  });
  expect(flights).toHaveBeenCalledTimes(count);
  expect(runApi.getRun).toHaveBeenCalledTimes(reads);
});
it("X removes the logical child and all content, retains a blank shell and stale close cannot hide a reopened Run", () => {
  vi.useFakeTimers();
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => {
    open("A");
    open("B");
  });
  act(() => flights.mock.calls.at(-1)![0].arrive());
  fireEvent.click(screen.getByRole("button", { name: "关闭执行详情" }));
  expect(useShell.getState().route.inspector).toBeUndefined();
  expect(screen.queryByRole("complementary")).toBeNull();
  expect(document.querySelector(".hp-inspector-exit")).toHaveAttribute("inert");
  expect(document.querySelector(".hp-inspector-exit")?.textContent).toBe("");
  act(() => {
    vi.advanceTimersByTime(200);
    open("C");
    vi.advanceTimersByTime(800);
  });
  expect(useShell.getState().route.inspector?.objectId).toBe("C");
  expect(screen.getByRole("complementary")).toBeInTheDocument();
  expect(document.querySelector(".hp-inspector-exit")).toBeNull();
  vi.useRealTimers();
});
it("guard never launches or changes visible target/history; confirmed child keeps its parent", () => {
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  act(() => flights.mock.calls.at(-1)![0].arrive());
  const count = flights.mock.calls.length;
  act(() => {
    useShell.setState({ dirtyTaskEditor: "draft" });
    useShell.getState().openInspector({ kind: "run", objectId: "B" }, true);
  });
  expect(useShell.getState().route.inspector?.objectId).toBe("A");
  expect(useShell.getState().backStack).toEqual([]);
  expect(flights).toHaveBeenCalledTimes(count);
  act(() => {
    const next = useShell.getState().pendingRoute!;
    useShell.setState({ dirtyTaskEditor: null });
    useShell.getState().navigate(next);
  });
  expect(useShell.getState().backStack.map((i) => i.objectId)).toEqual(["A"]);
});
it("live reduction and compact mode settle without replay, and close removes the shell immediately", () => {
  const view = render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  const count = flights.mock.calls.length;
  act(() => setMotionPreference("reduce"));
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "执行详情");
  act(() => setMotionPreference("system"));
  view.rerender(<InspectorHost compact onSaveHtml={vi.fn()} />);
  expect(flights).toHaveBeenCalledTimes(count);
  fireEvent.click(screen.getByRole("button", { name: "关闭执行详情" }));
  expect(document.querySelector(".hp-inspector-exit")).toBeNull();
});
it("same object repeated open and missing source never replay; old object query callbacks remain invalid", () => {
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  const flight = flights.mock.calls.at(-1)![0];
  act(() => {
    flight.arrive();
    flight.settle();
    open("A");
  });
  expect(flights).toHaveBeenCalledTimes(1);
  act(() =>
    useShell.getState().navigate({ screen: "ai", inspector: { kind: "run", objectId: "B" } }),
  );
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "执行详情");
  expect(flights).toHaveBeenCalledTimes(1);
  act(() => flight.arrive());
  expect(useRunInspector.getState().runId).toBe("B");
});

it("account/session identity invalidates an old arrival and remounts only the query boundary", () => {
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  const a = flights.mock.calls.at(-1)![0];
  const reads = vi.mocked(runApi.getRun).mock.calls.length;
  act(() => useAuth.setState({ account: { account_id: "new-session" } as never }));
  expect(runApi.getRun).toHaveBeenCalledTimes(reads + 1);
  act(() => {
    a.arrive();
    a.settle();
  });
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "执行详情");
  expect(flights).toHaveBeenCalledTimes(1);
  act(() => useAuth.setState({ account: null }));
});

it("neutral Inspector focus cannot scroll the source while the relay starts", () => {
  const focus = vi.spyOn(HTMLElement.prototype, "focus");
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  expect(document.getElementById("inspector-title")).toHaveFocus();
  expect(focus).toHaveBeenCalledWith({ preventScroll: true });
});

it("title finishes before details; current data starts immediately and later data never flashes the title again", async () => {
  const animations = titleAnimations();
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  expect(runApi.getRun).toHaveBeenCalledWith("A", expect.any(AbortSignal));
  const flight = flights.mock.calls.at(-1)![0];
  expect(flight.target).toBe(document.getElementById("inspector-run-title"));
  act(() => flight.arrive(true));
  const title = document.getElementById("inspector-run-title")!;
  expect(title).toHaveTextContent("执行对象");
  expect(title).toHaveClass("hp-inspector-title-arrival");
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("inert");
  act(() => flight.settle());
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  await finishTitle(animations);
  expect(title).not.toHaveClass("hp-inspector-title-arrival");
  expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
  expect(screen.getByText("正在加载执行…")).toBeVisible();
  act(() =>
    useRunInspector.setState({
      runId: "A",
      snapshot: {
        source_kind: "chat",
        run: { run_id: "A", status: "succeeded" },
        assistant_message: { content: "已加载" },
      } as never,
      loading: false,
    }),
  );
  expect(screen.getByText("已加载")).toBeVisible();
  expect(title).not.toHaveClass("hp-inspector-title-arrival");
  expect(animations.filter((e) => e.node.id === "inspector-run-title")).toHaveLength(1);
});

it("an old title completion cannot reveal a switched or reopened object", async () => {
  const animations = titleAnimations();
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  act(() => flights.mock.calls.at(-1)![0].arrive(true));
  act(() => open("B"));
  await finishTitle(animations, 0);
  expect(document.getElementById("inspector-title")).toHaveTextContent("接纳中");
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  act(() => flights.mock.calls.at(-1)![0].arrive(true));
  act(() => useShell.getState().closeInspector());
  act(() => open("C"));
  await finishTitle(animations, 1);
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  expect(useShell.getState().route.inspector?.objectId).toBe("C");
  act(() => flights.mock.calls.at(-1)![0].arrive(true));
  await finishTitle(animations, 2);
  expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
});

it("reduction during title reception releases the correct current details without waiting", () => {
  titleAnimations();
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  act(() => flights.mock.calls.at(-1)![0].arrive(true));
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  act(() => setMotionPreference("reduce"));
  expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
  expect(document.getElementById("inspector-title")).not.toHaveClass("hp-inspector-title-arrival");
});

it("safety fallback never claims actual arrival, and closing before arrival has no return filament", () => {
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
  act(() => open("A"));
  act(() => flights.mock.calls.at(-1)![0].arrive(false));
  expect(document.getElementById("inspector-title")).not.toHaveClass("hp-inspector-accept");
  const count = flights.mock.calls.length;
  act(() => useShell.getState().closeInspector());
  expect(flights).toHaveBeenCalledTimes(count);
  expect(document.querySelector(".hp-inspector-exit")).toHaveAttribute("inert");
});
