import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunInspector } from "./RunInspector";
import { runApi, useRunInspector } from "../../store/runInspector";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { useTraceStore } from "../trace/traceStore";
import { api } from "../../api/client";
import { RunApprovals } from "./RunResources";
import type { HpRunSnapshot, HpTraceTree } from "../../api/types";
const snapshot: HpRunSnapshot = {
  source_kind: "work",
  run: {
    source_kind: "work",
    run_id: "work-run",
    execution_id: "execution",
    work_id: "work",
    requirement_revision: 2,
    work_control_epoch: 1,
    conversation_id: null,
    session_id: null,
    status: "succeeded",
    version: 2,
    created_at: "2026-10-07T00:00:00Z",
    started_at: null,
    finished_at: "2026-10-07T00:00:02Z",
    updated_at: "2026-10-07T00:00:02Z",
    failure_code: null,
    failure_message: null,
    strategy_kind: "generic_agent",
    executor_key: "general",
    result_json: { summary: "actual projected result" },
    budget: null,
    branches: [],
  },
};
function Harness() {
  const inspector = useShell((s) => s.route.inspector);
  return inspector && <RunInspector inspector={inspector} />;
}
beforeEach(() => {
  useWorkbench.getState().reset();
  useRunInspector.getState().reset();
  useShell.getState().reset();
  useTraceStore.getState().reset();
});
afterEach(() => vi.restoreAllMocks());
it("loads a Work Run independently without assistant_message and returns to its Work controls", async () => {
  const get = vi.spyOn(runApi, "getRun").mockResolvedValue(snapshot);
  act(() => useShell.getState().openInspector({ kind: "run", objectId: "work-run" }));
  render(<Harness />);
  expect(await screen.findByText("本次执行状态独立于所属任务状态。")).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(1);
  expect(useWorkbench.getState().activeRun).toBeNull();
  expect(useWorkbench.getState().messages).toEqual([]);
  expect(screen.queryByTestId("stop-run")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看所属任务" }));
  await waitFor(() =>
    expect(useShell.getState().route).toMatchObject({
      screen: "tasks",
      workId: "work",
    }),
  );
  expect(useShell.getState().route.inspector).toBeUndefined();
});
it("keeps diagnostics lazy while navigating the overview", async () => {
  vi.spyOn(runApi, "getRun").mockResolvedValue(snapshot);
  const trace = vi.spyOn(useTraceStore.getState(), "loadTrace");
  const list = vi.spyOn(runApi, "listRunModelInputs");
  act(() => useShell.getState().openInspector({ kind: "run", objectId: "work-run" }));
  render(<Harness />);
  await screen.findByRole("button", { name: "查看所属任务" });
  expect(trace).not.toHaveBeenCalled();
  expect(list).not.toHaveBeenCalled();
});

describe("Run polling lifecycle", () => {
  let hidden = false;
  const running: HpRunSnapshot = { ...snapshot, run: { ...snapshot.run, status: "running" } };
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => {
      resolve = r;
    });
    return { promise, resolve };
  }
  async function tick(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }
  function visibility(value: boolean) {
    hidden = value;
    act(() => document.dispatchEvent(new Event("visibilitychange")));
  }
  function open(tab: "overview" | "advanced" = "overview") {
    act(() => useShell.getState().openInspector({ kind: "run", objectId: "work-run", tab }));
    return render(<Harness />);
  }
  beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });
  it("polls visible nonterminal snapshots recursively without overlap and stops at terminal", async () => {
    const first = deferred<HpRunSnapshot>();
    const second = deferred<HpRunSnapshot>();
    const get = vi
      .spyOn(runApi, "getRun")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    open();
    expect(get).toHaveBeenCalledTimes(1);
    await tick(10000);
    visibility(false);
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve(running));
    await tick(1999);
    expect(get).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(get).toHaveBeenCalledTimes(2);
    await tick(10000);
    visibility(false);
    expect(get).toHaveBeenCalledTimes(2);
    await act(async () => second.resolve(snapshot));
    await tick(20000);
    expect(get).toHaveBeenCalledTimes(2);
    expect(useRunInspector.getState().snapshot?.run.status).toBe("succeeded");
  });
  it("pauses hidden queries, resumes immediately, and discards a late response after close", async () => {
    const late = deferred<HpRunSnapshot>();
    const get = vi
      .spyOn(runApi, "getRun")
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(running)
      .mockReturnValueOnce(late.promise);
    open();
    await act(async () => {});
    expect(get).toHaveBeenCalledTimes(1);
    visibility(true);
    await tick(20000);
    expect(get).toHaveBeenCalledTimes(1);
    visibility(false);
    await act(async () => {});
    expect(get).toHaveBeenCalledTimes(2);
    await tick(2000);
    expect(get).toHaveBeenCalledTimes(3);
    act(() => useShell.getState().closeInspector());
    expect(get.mock.calls[2]?.[1]?.aborted).toBe(true);
    await act(async () => late.resolve(snapshot));
    await tick(20000);
    visibility(false);
    expect(get).toHaveBeenCalledTimes(3);
    expect(useRunInspector.getState().snapshot).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
  it("backs off a failed query before retrying and stops once the retry is terminal", async () => {
    const get = vi
      .spyOn(runApi, "getRun")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(snapshot);
    open();
    await act(async () => {});
    expect(screen.getByText("待同步，请重试。")).toBeInTheDocument();
    await tick(3999);
    expect(get).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(get).toHaveBeenCalledTimes(2);
    await tick(20000);
    expect(get).toHaveBeenCalledTimes(2);
  });
  it("keeps Trace polling singular across visibility changes and stops on terminal or close", async () => {
    const tree: HpTraceTree = {
      run: {
        trace_run_id: "trace",
        run_id: "work-run",
        conversation_id: null,
        strategy: "generic_agent",
        status: "running",
        started_at: "2026-10-07T00:00:00Z",
        ended_at: null,
        metadata: {},
      },
      roots: [],
    };
    const pending = deferred<HpTraceTree>();
    const get = vi.spyOn(runApi, "getRun").mockResolvedValue(running);
    vi.spyOn(runApi, "listRunModelInputs").mockResolvedValue({ visibility: "summary", items: [] });
    const trace = vi
      .spyOn(api, "request")
      .mockResolvedValue(tree)
      .mockResolvedValueOnce(tree)
      .mockReturnValueOnce(pending.promise);
    open("advanced");
    await act(async () => {});
    expect(trace).toHaveBeenCalledTimes(1);
    await tick(5000);
    expect(trace).toHaveBeenCalledTimes(2);
    visibility(true);
    visibility(false);
    await tick(1000);
    expect(trace).toHaveBeenCalledTimes(2);
    await act(async () => pending.resolve(tree));
    await tick(4999);
    expect(trace).toHaveBeenCalledTimes(2);
    await tick(1);
    expect(trace).toHaveBeenCalledTimes(3);
    visibility(true);
    await tick(20000);
    expect(trace).toHaveBeenCalledTimes(3);
    visibility(false);
    await act(async () => {});
    expect(trace).toHaveBeenCalledTimes(4);
    get.mockResolvedValue(snapshot);
    await act(async () => {
      await useRunInspector.getState().load();
    });
    await tick(20000);
    expect(trace).toHaveBeenCalledTimes(4);
    act(() => useShell.getState().closeInspector());
    await tick(20000);
    visibility(false);
    expect(trace).toHaveBeenCalledTimes(4);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("polls pending approvals on a terminal Run without overlap, pauses hidden, and stops after resolution", async () => {
    const approval = {
      approval_id: "approval",
      run_id: "work-run",
      operation_id: "operation",
      action_summary: "synthetic action",
      tool_name: "write",
      status: "pending" as const,
      expires_at: "2099-01-01T00:00:00Z",
    };
    const pending = deferred<{ approvals: (typeof approval)[] }>();
    const list = vi
      .spyOn(runApi, "listRunFileApprovals")
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValue({ approvals: [{ ...approval, status: "approved" }] });
    useRunInspector.getState().select("work-run");
    useRunInspector.setState({ approvals: [approval] });
    const view = render(<RunApprovals terminal />);
    expect(list).toHaveBeenCalledTimes(1);
    await tick(20000);
    visibility(false);
    expect(list).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve({ approvals: [approval] }));
    visibility(true);
    await tick(20000);
    expect(list).toHaveBeenCalledTimes(1);
    visibility(false);
    await act(async () => {});
    expect(screen.getByText("已授权，等待执行状态更新")).toBeInTheDocument();
    const resolvedCalls = list.mock.calls.length;
    await tick(20000);
    expect(list).toHaveBeenCalledTimes(resolvedCalls);
    view.unmount();
    visibility(false);
    await tick(20000);
    expect(list).toHaveBeenCalledTimes(resolvedCalls);
    expect(vi.getTimerCount()).toBe(0);
  });
});
