import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useVitality, useApprovalFact } from "./useVitality";
import { useRunInspector } from "../../store/runInspector";
import { PROGRESS_FRESH_MS } from "./policy";
afterEach(() => {
  vi.useRealTimers();
  useRunInspector.getState().select("");
});
it("expires phase once and removes its timer on unmount", () => {
  vi.useFakeTimers();
  vi.setSystemTime(100_000);
  const progress = {
    phase: "calling_model",
    summary: "模型处理中",
    scope: { runId: "R", generation: 7, receivedAt: Date.now() },
  };
  const { result, unmount } = renderHook(() =>
    useVitality({ run_id: "R", status: "running" }, progress, 7, false),
  );
  expect(result.current.state).toBe("thinking");
  act(() => vi.advanceTimersByTime(PROGRESS_FRESH_MS));
  expect(result.current.state).toBe("running");
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
it("approval facts keep query ownership and never interpret unknown as none", () => {
  const { result, rerender } = renderHook(({ id }) => useApprovalFact(id), {
    initialProps: { id: "R" },
  });
  expect(result.current).toBe("unknown");
  act(() =>
    useRunInspector.setState({
      runId: "R",
      approvalLoaded: true,
      approvalError: null,
      unavailable: false,
      approvals: [],
    }),
  );
  expect(result.current).toBe("none");
  act(() =>
    useRunInspector.setState({
      approvals: [{ status: "pending" }] as ReturnType<
        typeof useRunInspector.getState
      >["approvals"],
    }),
  );
  expect(result.current).toBe("pending");
  rerender({ id: "OTHER" });
  expect(result.current).toBe("unknown");
  rerender({ id: "R" });
  act(() => useRunInspector.setState({ approvalError: "断连" }));
  expect(result.current).toBe("unknown");
});
