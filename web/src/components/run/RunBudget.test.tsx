import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, it, expect } from "vitest";
import { RunBudget } from "./RunBudget";
import { diagnosticBudget } from "../../../e2e/run-diagnostics-fixtures";
afterEach(cleanup);
it("uses the authoritative cumulative amount with independent request count and composition", () => {
  render(<RunBudget budget={diagnosticBudget} />);
  expect(screen.getByTestId("run-token-usage")).toHaveTextContent("12,480");
  expect(screen.getByRole("img", { name: /输入 10,860 Token，占 87.0%/ })).toBeInTheDocument();
  expect(
    screen.getByText("请求数按预算尝试统计，含重试、fallback 及未派发尝试。", { exact: false }),
  ).toBeInTheDocument();
  expect(screen.getByText("预算与策略").closest("details")).not.toHaveAttribute("open");
});
it("distinguishes missing, zero, estimates, partial coverage and in-flight requests", () => {
  const budget = structuredClone(diagnosticBudget);
  budget.usage_state = "partial";
  budget.has_estimates = true;
  budget.model_calls.in_flight = 1;
  budget.tokens.total.reserved = 2000;
  const { rerender } = render(<RunBudget budget={budget} />);
  expect(screen.getByTestId("run-token-usage")).toHaveTextContent("≈12,480");
  expect(screen.getByText(/部分用量无法确认/)).toHaveTextContent("1 次请求在途，未计入累计 Token");
  budget.tokens.total.used = 0;
  budget.tokens.input.used = 0;
  budget.tokens.output.used = 0;
  rerender(<RunBudget budget={budget} />);
  expect(screen.getByTestId("run-token-usage")).toHaveTextContent("≈0");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  budget.usage_state = "none";
  rerender(<RunBudget budget={budget} />);
  expect(screen.getByTestId("run-token-usage")).toHaveTextContent("—");
  rerender(<RunBudget budget={null} />);
  expect(screen.getByText("本次执行暂无用量记录。")).toBeInTheDocument();
  expect(screen.queryByTestId("run-token-usage")).not.toBeInTheDocument();
});
it("does not normalize mismatched scopes into a composition", () => {
  const budget = structuredClone(diagnosticBudget);
  budget.tokens.total.used += 10;
  render(<RunBudget budget={budget} />);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText(/输入输出与总量口径不同/)).toBeInTheDocument();
});
it("keeps zero reservation, unknown limit and zero allowance distinct, and discloses overruns", () => {
  const budget = structuredClone(diagnosticBudget);
  budget.mode = "enforce";
  budget.tokens.total.limit = 10000;
  budget.tokens.input.limit = null;
  budget.tokens.output.limit = 0;
  render(<RunBudget budget={budget} />);
  expect(screen.getByText(/已用 12,480 · 预留 0 · 剩余 0 · 超出 2,480/)).toBeInTheDocument();
  expect(screen.getByText("10,860 / 未提供上限")).toBeInTheDocument();
  expect(screen.getByText("1,620 / 0")).toBeInTheDocument();
  expect(document.querySelectorAll(".hp-budget-track")).toHaveLength(1);
});
it("uses the backend final-response protection rule instead of an invented percent threshold", () => {
  const budget = structuredClone(diagnosticBudget);
  budget.mode = "enforce";
  budget.final_response_reserve_tokens = 2000;
  budget.tokens.total.limit = 14000;
  const { rerender } = render(<RunBudget budget={budget} />);
  expect(screen.getByText(/剩余额度已进入最终回复保护区间/)).toBeInTheDocument();
  budget.mode = "observe";
  rerender(<RunBudget budget={budget} />);
  expect(screen.queryByText(/剩余额度已进入最终回复保护区间/)).not.toBeInTheDocument();
  budget.status = "exhausted";
  rerender(<RunBudget budget={budget} />);
  expect(screen.getByText(/当前策略不执行 Run 额度拦截/)).toBeInTheDocument();
});
