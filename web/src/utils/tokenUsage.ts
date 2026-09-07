import type { HpRunBudget } from "../api/types";

export function formatTokenCount(value: number): string {
  if (value < 1_000) return String(value);
  const scaled = value / 1_000;
  return `${scaled >= 10 || Number.isInteger(scaled) ? scaled.toFixed(0) : scaled.toFixed(1)}k`;
}

export function tokenUsagePrefix(budget: HpRunBudget): string {
  return budget.has_estimates ? "≈" : "";
}
