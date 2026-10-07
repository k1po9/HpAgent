import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { HpRun } from "../api/types";
import { RunStatus } from "./RunStatus";

function failedRun(retryable: boolean): HpRun {
  return {
    run_id: "run-1",
    conversation_id: "conversation-1",
    session_id: "session-1",
    trigger_message_id: "message-1",
    retry_of_run_id: null,
    agent_strategy: "react",
    status: "failed",
    failure: {
      code: retryable ? "model_unavailable" : "tool_side_effect_uncertain",
      message: "failed",
      retryable,
    },
    version: 1,
    created_at: "2026-08-17T00:00:00Z",
    started_at: "2026-08-17T00:00:01Z",
    finished_at: "2026-08-17T00:00:02Z",
    updated_at: "2026-08-17T00:00:02Z",
    budget: null,
  };
}

describe("RunStatus retry safety", () => {
  it("explains a Run that has remained queued", () => {
    vi.useFakeTimers();
    render(
      <RunStatus
        activeRun={{
          ...failedRun(true),
          status: "queued",
          created_at: "2020-01-01T00:00:00Z",
          failure: null,
        }}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByText("仍在等待执行 Worker 接管")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows the queued warning after 60 seconds", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-17T00:00:00Z"));
    render(
      <RunStatus
        activeRun={{ ...failedRun(true), status: "queued", failure: null }}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.queryByText("仍在等待执行 Worker 接管")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText("仍在等待执行 Worker 接管")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("hides Retry for a cancelled run", () => {
    render(
      <RunStatus
        activeRun={{ ...failedRun(true), status: "cancelled", failure: null }}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByTestId("run-label")).toHaveTextContent("已停止");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("hides Retry and explains an uncertain external side effect", () => {
    render(
      <RunStatus
        activeRun={failedRun(false)}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("无法确认是否已完成的外部操作");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-spinner")).not.toBeInTheDocument();
  });

  it("keeps Retry for a safely retryable failure", () => {
    render(
      <RunStatus
        activeRun={failedRun(true)}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the safe model failure message and preserves retry rules", () => {
    const run = failedRun(true);
    run.failure!.message = "模型暂时不可用";
    const { rerender } = render(
      <RunStatus
        activeRun={run}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByTestId("run-failure-message")).toHaveTextContent("模型暂时不可用");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    rerender(
      <RunStatus
        activeRun={{ ...run, status: "cancelled", failure: null }}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("run-failure-message")).not.toBeInTheDocument();
  });
});
