import { StrictMode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { workFixture } from "./taskFixtures";
import { useTaskController } from "./useTaskController";
const originalLoad = useWorks.getState().load;
const feeds = vi.hoisted(() => ({ active: new Set<string>(), max: 0, opened: [] as string[] }));
vi.mock("../../sse/workFeed", () => ({
  subscribeWork: (id: string) => {
    feeds.active.add(id);
    feeds.opened.push(id);
    feeds.max = Math.max(feeds.max, feeds.active.size);
    return () => feeds.active.delete(id);
  },
}));
afterEach(() => {
  cleanup();
  useWorks.getState().reset();
  useShell.getState().reset();
  vi.restoreAllMocks();
  useWorks.setState({ load: originalLoad });
  vi.useRealTimers();
  feeds.active.clear();
  feeds.max = 0;
  feeds.opened = [];
});
it("bounds feeds to two, prioritizes selected paused task and releases on leaving", async () => {
  vi.spyOn(useWorks.getState(), "load").mockResolvedValue();
  useWorks.setState({
    items: [
      workFixture({ work_id: "a", active_coordinator_run_id: "r" }),
      workFixture({ work_id: "b", active_coordinator_run_id: "r2" }),
      workFixture({ work_id: "selected", status: "paused" }),
    ],
  });
  useShell.setState({
    route: { screen: "tasks", bucket: "active", inspector: { kind: "task", objectId: "selected" } },
  });
  const hook = renderHook(() => useTaskController(), { wrapper: StrictMode });
  await act(async () => {});
  expect(feeds.active.size).toBe(2);
  expect(feeds.active.has("selected")).toBe(true);
  expect(feeds.max).toBe(2);
  act(() => useShell.setState({ route: { screen: "ai" } }));
  expect(feeds.active.size).toBe(0);
  hook.unmount();
});
it("does not overlap slow scans; visibility pause and recovery synchronize snapshots", async () => {
  vi.useFakeTimers();
  let done!: () => void;
  const load = vi.spyOn(useWorks.getState(), "load").mockImplementation(
    () =>
      new Promise<void>((r) => {
        done = r;
      }),
  );
  useShell.setState({ route: { screen: "tasks" } });
  renderHook(() => useTaskController());
  await act(async () => vi.advanceTimersByTimeAsync(30000));
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => {
    done();
  });
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  expect(load).toHaveBeenCalledTimes(2);
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => {
    done();
    vi.advanceTimersByTime(30000);
  });
  expect(load).toHaveBeenCalledTimes(2);
  hidden.mockReturnValue(false);
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(load).toHaveBeenCalledTimes(3);
});
