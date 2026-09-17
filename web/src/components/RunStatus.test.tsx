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
    budget: null,
  };
}

describe("RunStatus retry safety", () => {
  it("shows used, reserved, estimated, and unmetered usage distinctly", () => {
    const activeRun = failedRun(true);
    activeRun.budget = {
      status: "ok",
      mode: "observe",
      policy_version: "web-token-v2",
      tokens: {
        input: { used: 1000, reserved: 0, limit: 10000 },
        output: { used: 234, reserved: 2000, limit: 10000 },
        total: { used: 1234, reserved: 2000, limit: 20000 },
      },
      model_calls: { settled: 2, in_flight: 1, unmetered: 1, total_attempts: 4, limit: 10 },
      by_source: {
        provider: { input_tokens: 900, output_tokens: 200, total_tokens: 1100 },
        measured: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
        estimated: { input_tokens: 100, output_tokens: 34, total_tokens: 134 },
      },
      usage_state: "partial",
      usage_quality: "mixed",
      has_estimates: true,
      model_total_tokens_used: 1234,
      model_total_tokens_limit: 20000,
      tool_calls_used: 0,
      tool_calls_limit: 0,
      bytes_scanned_used: 0,
      bytes_scanned_limit: 0,
    };
    render(
      <RunStatus
        activeRun={activeRun}
        busyMessage={null}
        progress={null}
        degraded={false}
        stopping={false}
        onStop={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByTestId("run-token-usage")).toHaveTextContent(
      "≈1.2k tokens · ≤2k 预留 · 4 次模型请求 · 部分用量无法确认",
    );
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
});
