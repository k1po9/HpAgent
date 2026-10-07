import { useState } from "react";
import type { HpWork } from "../../api/types";
import { useWorks } from "../../store/works";
import { Surface } from "../shell/Surface";
import { canRetryDelivery, validSnapshot } from "./taskActions";
import { useTaskOperations } from "./taskOperations";
const labels: Record<string, string> = {
  pending: "等待发送",
  sending: "正在发送，等待回执",
  accepted: "渠道已接受",
  failed: "发送失败",
  uncertain: "发送结果待确认",
  cancelled: "已撤销",
};
export function TaskDeliveryDecision({
  work,
  delivery,
}: {
  work: HpWork;
  delivery: HpWork["deliveries"][number];
}) {
  const [outcome, setOutcome] = useState<
    "accepted" | "not_sent" | "retry_accepting_duplicate_risk" | null
  >(null);
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const mutable =
    validSnapshot(work) &&
    !["stopped", "completed"].includes(work.status) &&
    ["failed", "uncertain"].includes(delivery.state);
  const current = delivery.requirement_revision === work.current_requirement_revision;
  return (
    <article className="hp-task-delivery">
      <h4>
        {delivery.channel === "web" ? "账户收件箱" : delivery.channel === "qq" ? "QQ" : "其他渠道"}{" "}
        · {labels[delivery.state] ?? "状态待核实"}
      </h4>
      <p>
        {current ? "当前要求" : "历史要求"} r{delivery.requirement_revision} ·{" "}
        {delivery.purpose === "fact" ? "事实通知" : "任务履行投递"}
      </p>
      {delivery.provider_receipt?.level && (
        <p>
          {delivery.provider_receipt.level === "account_inbox_committed"
            ? "已存入账户收件箱"
            : delivery.provider_receipt.level === "explicitly_confirmed"
              ? "用户已确认发送发生"
              : "渠道回执已记录"}
          ；不代表接收者已读。
        </p>
      )}
      {delivery.last_error && <p>失败原因：{delivery.last_error}</p>}
      {mutable && (
        <div className="hp-task-actions">
          <button
            disabled={intent?.busy || intent?.uncertain}
            onClick={() => setOutcome("accepted")}
          >
            确认已送达
          </button>
          <button
            disabled={intent?.busy || intent?.uncertain}
            onClick={() => setOutcome("not_sent")}
          >
            确认未发送
          </button>
          {canRetryDelivery(work, delivery) && (
            <button
              disabled={intent?.busy || intent?.uncertain}
              onClick={() => setOutcome("retry_accepting_duplicate_risk")}
            >
              接受重复风险并重试投递
            </button>
          )}
        </div>
      )}
      {outcome && (
        <Surface title="确认投递决策" onClose={() => setOutcome(null)}>
          <p>
            {work.title} · {delivery.channel} · 投递 {delivery.delivery_id}
          </p>
          <p>
            {outcome === "accepted"
              ? "确认发送已经发生，将记录明确确认回执；不代表已读。"
              : outcome === "not_sent"
                ? "当前有效投递可能回到等待发送并重新尝试；历史或失效投递会撤销。实际后果以服务器返回状态为准。"
                : "重新发送可能造成重复通知。仅在你接受重复发送风险时确认，渠道授权和执行版本由服务器再次核验。"}
          </p>
          <button
            disabled={
              intent?.busy ||
              !mutable ||
              (outcome === "retry_accepting_duplicate_risk" && !canRetryDelivery(work, delivery))
            }
            onClick={() =>
              void useWorks
                .getState()
                .resolveDelivery(work, delivery.delivery_id, outcome)
                .then((ok) => {
                  if (ok) setOutcome(null);
                })
            }
          >
            {outcome === "retry_accepting_duplicate_risk"
              ? "确认承担重复风险并重试"
              : "确认此投递决策"}
          </button>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
    </article>
  );
}
