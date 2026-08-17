import { render, screen } from "@testing-library/react";
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
  };
}

describe("RunStatus retry safety", () => {
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
});
