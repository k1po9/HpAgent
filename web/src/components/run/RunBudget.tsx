import type { HpRunBudget } from "../../api/types";
import { formatTokenCount, tokenUsagePrefix } from "../../utils/tokenUsage";
export function RunBudget({ budget }: { budget: HpRunBudget | null }) {
  if (!budget) return <p>暂无预算记录。</p>;
  return (
    <details>
      <summary>预算与模型用量</summary>
      {budget.usage_state !== "none" && (
        <p data-testid="run-token-usage">
          {tokenUsagePrefix(budget)}
          {formatTokenCount(budget.tokens.total.used)} tokens
          {budget.tokens.total.reserved > 0
            ? ` · ≤${formatTokenCount(budget.tokens.total.reserved)} 预留`
            : ""}
          {budget.model_calls.total_attempts > 0
            ? ` · ${budget.model_calls.total_attempts} 次模型请求`
            : ""}
          {budget.model_calls.unmetered > 0 ? " · 部分用量无法确认" : ""}
        </p>
      )}
      <pre>{JSON.stringify(budget, null, 2)}</pre>
    </details>
  );
}
