import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { RunBudget } from "./RunBudget";
import type { HpRunBudget } from "../../api/types";
it("shows used, reserved, estimated, and unmetered usage distinctly", () => {
  const activeRun: { budget: HpRunBudget | null } = { budget: null };
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
  render(<RunBudget budget={activeRun.budget} />);
  expect(screen.getByTestId("run-token-usage")).toHaveTextContent(
    "≈1.2k tokens · ≤2k 预留 · 4 次模型请求 · 部分用量无法确认",
  );
});
