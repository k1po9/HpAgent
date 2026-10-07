import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RunInspector } from "./RunInspector";
import { runApi, useRunInspector } from "../../store/runInspector";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { useTraceStore } from "../trace/traceStore";
import type { HpRunSnapshot } from "../../api/types";
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
