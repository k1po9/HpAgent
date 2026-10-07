import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ExecutionBlock } from "./ExecutionBlock";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import type { HpRun } from "../../api/types";
afterEach(() => vi.restoreAllMocks());
beforeEach(() => {
  useWorkbench.getState().reset();
  useShell.getState().reset();
});
it("does not infer history success from a completed message or another active Run", () => {
  useWorkbench.setState({ activeRun: { run_id: "A", status: "running" } as HpRun });
  render(<ExecutionBlock runId="B" messageId="message-B" />);
  expect(screen.queryByTestId("run-label")).not.toBeInTheDocument();
  expect(screen.queryByTestId("stop-run")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看执行详情" }));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "run",
    objectId: "B",
    origin: { messageId: "message-B" },
  });
});
it.each([
  ["succeeded", "✓"],
  ["failed", "!"],
  ["cancelled", "■"],
] as const)("distinguishes %s terminal marker", (status, icon) => {
  useWorkbench.setState({ activeRun: { run_id: "A", status, failure: null } as HpRun });
  render(<ExecutionBlock runId="A" />);
  expect(screen.getByText(icon)).toBeInTheDocument();
  expect(screen.queryByTestId("run-token-usage")).not.toBeInTheDocument();
});

it.each(["stop", "retry"] as const)(
  "does not let a stale %s button target a newly selected Run",
  (action) => {
    const run = {
      run_id: "A",
      status: action === "stop" ? "running" : "failed",
      failure: { code: "model_unavailable", message: "failed", retryable: true },
    } as HpRun;
    useWorkbench.setState({ activeRun: run });
    render(<ExecutionBlock runId="A" />);
    const command = vi.fn();
    const current = useWorkbench.getState();
    // The click-time authority may change before React commits a new message render.
    vi.spyOn(useWorkbench, "getState").mockReturnValue({
      ...current,
      activeRun: { ...run, run_id: "B" },
      stopRun: command,
      retryRun: command,
    });
    fireEvent.click(screen.getByTestId(action === "stop" ? "stop-run" : "retry-run"));
    expect(command).not.toHaveBeenCalled();
  },
);
