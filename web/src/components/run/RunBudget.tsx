import { Button } from "../ui";
import { Cpu, ArrowDownLeft, ArrowUpRight, ChevronRight } from "lucide-react";
import type { HpRunBudget, HpUsageCounter } from "../../api/types";
import { useTraceStore } from "../trace/traceStore";

const count = (n: number) => n.toLocaleString("en-US");
const limitLabel = (n: number | null) => (n === null ? "未提供上限" : count(n));
const valid = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n) && n >= 0;

function BudgetLine({
  label,
  value,
  enforced,
}: {
  label: string;
  value: HpUsageCounter;
  enforced: boolean;
}) {
  const { used, reserved, limit } = value;
  const known = valid(used) && valid(reserved) && valid(limit);
  const occupied = used + reserved;
  // A zero limit is an actual zero allowance, never an invented unlimited budget.
  const remaining = known ? Math.max(0, limit! - occupied) : null;
  return (
    <div className="hp-budget-line">
      <div>
        <span>{label}</span>
        <span>{valid(used) ? `${count(used)} / ${limitLabel(limit)}` : "未提供完整额度"}</span>
      </div>
      {known && limit! > 0 && (
        <div
          className="hp-budget-track"
          aria-label={`${label}：已用 ${used}，预留 ${reserved}，${enforced ? "剩余" : "距观察额度"} ${remaining}`}
        >
          <i style={{ width: `${Math.min(100, (used / limit!) * 100)}%` }} />
          <i
            className="hp-budget-reserved"
            style={{
              width: `${Math.min(100 - Math.min(100, (used / limit!) * 100), (reserved / limit!) * 100)}%`,
            }}
          />
        </div>
      )}
      {known ? (
        <small>
          已用 {count(used)} · 预留 {count(reserved)} · {enforced ? "剩余" : "距观察额度"}{" "}
          {count(remaining!)}
          {occupied > limit! ? ` · 超出 ${count(occupied - limit!)}` : ""}
        </small>
      ) : (
        <small>预留 {valid(reserved) ? count(reserved) : "未记录"} · 未计算占用比例与剩余</small>
      )}
    </div>
  );
}

function ModelCalls() {
  const nodes = useTraceStore((s) => s.nodes);
  const loading = useTraceStore((s) => s.loading);
  const selected = useTraceStore((s) => s.selectNode);
  const calls = Object.values(nodes).filter(
    (node) =>
      node.type === "llm" &&
      typeof node.metadata.model_call_id === "string" &&
      node.metadata.model_call_id.length > 0,
  );
  return (
    <div className="hp-usage-calls">
      <p className="hp-diagnostic-note">
        仅列出带请求标识的模型轨迹，可能不含全部重试与
        fallback。节点耗时包含该段执行过程，不等于单次请求延迟；累计以上方账本为准。
      </p>
      {calls.length ? (
        calls.map((node) => {
          const raw = node.metadata.token_usage;
          const usage = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : null;
          const total = usage?.total_tokens;
          return (
            <article key={node.id} data-status={node.status}>
              <div>
                <strong>
                  {typeof node.metadata.model === "string" ? node.metadata.model : "模型未记录"}
                </strong>
                <small>
                  {
                    {
                      completed: "已完成",
                      running: "运行中",
                      failed: "失败",
                      cancelled: "已取消",
                      unknown: "状态未知",
                    }[node.status]
                  }
                </small>
              </div>
              <code title="真实模型请求标识">{String(node.metadata.model_call_id)}</code>
              <span>
                {node.metadata.fallback_attempt
                  ? `尝试 ${String(node.metadata.fallback_attempt)} · `
                  : ""}
                {valid(total)
                  ? `${usage?.usage_source === "estimated" ? "≈" : ""}${count(total)} Token`
                  : "用量未记录"}{" "}
                · 节点{" "}
                {node.durationMs === null
                  ? "耗时未记录"
                  : `${(node.durationMs / 1000).toFixed(2)} s`}
              </span>
              <Button
                type="button"
                onClick={() => {
                  selected(node.id);
                  document.querySelector(".hp-trace-detail")?.scrollIntoView({ block: "nearest" });
                }}
              >
                查看节点 <ArrowUpRight size={13} />
              </Button>
            </article>
          );
        })
      ) : (
        <p className="hp-diagnostic-note">
          {loading ? "正在读取可关联的模型轨迹…" : "暂无可可靠关联的调用明细。"}
        </p>
      )}
    </div>
  );
}

export function RunBudget({ budget }: { budget: HpRunBudget | null }) {
  if (!budget)
    return (
      <section className="hp-run-usage" aria-label="模型用量">
        <h3>模型用量</h3>
        <p className="hp-diagnostic-note">本次执行暂无用量记录。</p>
        <details>
          <summary>
            <ChevronRight size={14} />
            模型调用明细
          </summary>
          <ModelCalls />
        </details>
      </section>
    );
  const total = budget.tokens.total.used;
  const input = budget.tokens.input.used;
  const output = budget.tokens.output.used;
  const metered = budget.usage_state !== "none" && (budget.model_calls.settled > 0 || total > 0);
  const split =
    metered &&
    valid(total) &&
    valid(input) &&
    valid(output) &&
    total > 0 &&
    input + output === total;
  const enforced = budget.mode === "enforce";
  const constrained = budget.status === "exhausted";
  const protectedReserve = budget.final_response_reserve_tokens;
  const nearLimit =
    enforced &&
    valid(protectedReserve) &&
    protectedReserve > 0 &&
    [budget.tokens.total, budget.tokens.output].some(
      (value) =>
        valid(value.limit) && value.used + value.reserved + protectedReserve >= value.limit,
    );
  return (
    <section className="hp-run-usage" aria-label="模型用量">
      <div className="hp-usage-heading">
        <h3>模型用量</h3>
        <span>本次执行累计</span>
      </div>
      <div className="hp-usage-scale">
        <div>
          <span className="hp-diagnostic-eyebrow">累计 Token</span>
          <strong data-testid="run-token-usage">
            {metered && valid(total) ? `${budget.has_estimates ? "≈" : ""}${count(total)}` : "—"}
          </strong>
        </div>
        <div className="hp-usage-requests">
          <span className="hp-diagnostic-eyebrow">
            <Cpu size={14} />
            模型请求
          </span>
          <strong>
            {count(budget.model_calls.total_attempts)}
            <small>次</small>
          </strong>
        </div>
      </div>
      <div className="hp-usage-composition">
        {split && (
          <div
            className="hp-usage-bar"
            role="img"
            aria-label={`输入 ${count(input)} Token，占 ${((input / total) * 100).toFixed(1)}%；输出 ${count(output)} Token，占 ${((output / total) * 100).toFixed(1)}%`}
          >
            <i style={{ width: `${(input / total) * 100}%` }} />
            <i style={{ width: `${(output / total) * 100}%` }} />
          </div>
        )}
        <div className="hp-usage-legend">
          <span>
            <ArrowDownLeft size={14} />
            输入 <b>{metered && valid(input) ? count(input) : "—"}</b>
            {split && <small>{((input / total) * 100).toFixed(1)}%</small>}
          </span>
          <span>
            <ArrowUpRight size={14} />
            输出 <b>{metered && valid(output) ? count(output) : "—"}</b>
            {split && <small>{((output / total) * 100).toFixed(1)}%</small>}
          </span>
        </div>
      </div>
      <p className="hp-diagnostic-note hp-usage-quality">
        {budget.has_estimates
          ? "包含估算用量"
          : budget.usage_quality === "provider"
            ? "供应商已报告用量"
            : metered
              ? "账本已记录用量"
              : "尚无已结算用量"}
        {budget.usage_state === "partial" ? " · 部分用量无法确认" : ""}
        {budget.model_calls.in_flight > 0
          ? ` · ${count(budget.model_calls.in_flight)} 次请求在途，未计入累计 Token`
          : ""}
        {metered && total !== input + output ? " · 输入输出与总量口径不同，未绘制比例" : ""}
        。请求数按预算尝试统计，含重试、fallback 及未派发尝试。
      </p>
      {constrained && (
        <p className="hp-usage-constraint" role="status">
          {enforced
            ? "执行已触及预算限制，请展开查看额度与策略。"
            : "用量已触及观察额度；当前策略不执行 Run 额度拦截。"}
        </p>
      )}
      {!constrained && nearLimit && (
        <p className="hp-usage-constraint" role="status">
          剩余额度已进入最终回复保护区间，请展开查看预算与策略。
        </p>
      )}
      <div className="hp-usage-disclosures">
        <details>
          <summary>
            <ChevronRight size={14} />
            模型调用明细<span>按关联记录查看</span>
          </summary>
          <ModelCalls />
        </details>
        <details>
          <summary>
            <ChevronRight size={14} />
            预算与策略
            <span>
              {enforced ? "强制限制" : budget.mode === "observe" ? "观察模式" : "限制关闭"}
              {constrained ? " · 已触及额度" : ""}
            </span>
          </summary>
          <div className="hp-budget-policy">
            <p className="hp-diagnostic-note">
              {enforced
                ? "以下为本次 Run 的额度。最终回复保护额度、其他维度或任务／账号限制也可能影响后续请求；不推算可继续请求次数。"
                : "以下为策略观察额度，不代表正在生效的 Run 硬限制。"}
            </p>
            <BudgetLine label="累计 Token" value={budget.tokens.total} enforced={enforced} />
            <BudgetLine label="输入 Token" value={budget.tokens.input} enforced={enforced} />
            <BudgetLine label="输出 Token" value={budget.tokens.output} enforced={enforced} />
            {valid(protectedReserve) && (
              <p className="hp-diagnostic-note">
                最终回复保护额度：{count(protectedReserve)} Token（策略保护额度，不计入已预留）。
              </p>
            )}
            <p className="hp-diagnostic-note">
              模型请求：{count(budget.model_calls.total_attempts)} /{" "}
              {limitLabel(budget.model_calls.limit)} · 工具调用：{count(budget.tool_calls_used)} /{" "}
              {count(budget.tool_calls_limit)}
            </p>
            <small className="hp-diagnostic-note">策略版本 {budget.policy_version}</small>
          </div>
        </details>
        <details>
          <summary>
            <ChevronRight size={14} />
            原始数据<span>用量与预算</span>
          </summary>
          <pre aria-label="用量与预算原始数据">{JSON.stringify(budget, null, 2)}</pre>
        </details>
      </div>
    </section>
  );
}
