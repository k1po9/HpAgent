import { useShell } from "../store/shell";
import { useEffect, useRef } from "react";
import { Button, Flex, Text } from "@radix-ui/themes";
import { useWorks } from "../store/works";
import { useArtifacts } from "../store/artifacts";
import { subscribeWork } from "../sse/workFeed";

const statusLabels = {
  active: "持续履行",
  pausing: "正在暂停",
  paused: "已暂停",
  stopping: "正在停止",
  stopped: "已停止",
  completed: "已完成",
};
const deliveryLabels: Record<string, string> = {
  pending: "等待投递",
  sending: "正在发送",
  accepted: "渠道已接受",
  failed: "投递失败",
  uncertain: "发送结果待确认",
  cancelled: "已撤销投递",
};
const continuationLabels: Record<string, string> = {
  ready: "准备继续",
  blocked: "需要处理",
  awaiting_input: "等待确认或补充",
  awaiting_delivery: "等待渠道回执",
  at_time: "等待下一次到期",
  retry_after: "等待重试",
  none: "履约结束",
};
const reasonLabels: Record<string, string> = {
  budget_exhausted: "累计额度已用尽",
  waiting_capacity: "等待执行名额",
  side_effect_uncertain: "有一次操作结果待确认",
  attempt_failed: "上次执行失败",
  attempt_cancelled: "上次执行已取消",
  user_acceptance_required: "请确认这份成果",
};

export function WorkPanel({
  conversationId,
  pageMode = false,
}: {
  conversationId: string | null;
  pageMode?: boolean;
}) {
  const locatedWork = useRef<string | null>(null);
  const selectedWork = useShell((s) => s.route.workId);
  const items = useWorks((s) => s.items);
  const busy = useWorks((s) => s.busy);
  const error = useWorks((s) => s.error);
  const load = useWorks((s) => s.load);
  const control = useWorks((s) => s.control);
  const link = useWorks((s) => s.link);
  const increaseBudget = useWorks((s) => s.increaseBudget);
  const resolveDelivery = useWorks((s) => s.resolveDelivery);
  const openArtifact = useArtifacts((s) => s.openArtifact);
  // Keep live connections bounded; the full list also recovers from PostgreSQL snapshots.
  const ids = items
    .filter((w) => ["active", "pausing", "stopping"].includes(w.status))
    .slice(0, 2)
    .map((w) => w.work_id)
    .join(",");
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => {
    const stops = ids
      ? ids.split(",").map((id) =>
          subscribeWork(
            id,
            () => useWorks.getState().cursors[id] ?? 0,
            (event) => useWorks.getState().receive(event),
            () => useWorks.getState().refresh(id),
          ),
        )
      : [];
    return () => stops.forEach((stop) => stop());
  }, [ids]);
  useEffect(() => {
    if (!selectedWork) {
      locatedWork.current = null;
      return;
    }
    if (locatedWork.current === selectedWork) return;
    const node = document.getElementById(`work-${selectedWork}`);
    if (node) {
      node.scrollIntoView?.({ block: "center" });
      locatedWork.current = selectedWork;
    }
  }, [selectedWork, items]);
  return (
    <details className="hp-work-panel" open={pageMode || undefined}>
      <summary>持续工作（{items.length}）</summary>
      {error && <p role="alert">{error}</p>}
      {items.length === 0 && <Text size="2">暂无持续委托。</Text>}
      {items.map((work) => (
        <article
          id={`work-${work.work_id}`}
          key={work.work_id}
          aria-label={work.title}
          className={selectedWork === work.work_id ? "hp-work-selected" : undefined}
        >
          <Flex justify="between">
            <strong>{work.title}</strong>
            <Text size="2">
              {statusLabels[work.status]} · r{work.current_requirement_revision}
            </Text>
          </Flex>
          <p>{work.requirement.objective}</p>
          <Text size="2">
            {work.status === "stopped" ? (
              "工作已停止，不再按原计划继续。"
            ) : work.status === "completed" ? (
              "工作已完成。"
            ) : (
              <>
                {work.status === "paused" ? "已暂停 · 原计划：" : ""}
                {work.active_coordinator_run_id ? "后台执行中 · " : ""}
                {continuationLabels[work.continuation.kind] ?? "等待继续"}
                {reasonLabels[work.continuation.reason]
                  ? ` · ${reasonLabels[work.continuation.reason]}`
                  : ""}
                {work.continuation.due_at
                  ? ` · ${new Date(work.continuation.due_at).toLocaleString()}`
                  : ""}
              </>
            )}
          </Text>
          {work.budget && (
            <p>
              累计模型额度：{work.budget.used.model_total_tokens ?? 0} /{" "}
              {work.budget.limits.model_total_tokens}，保留{" "}
              {work.budget.reserved.model_total_tokens ?? 0}
            </p>
          )}
          <Flex gap="2" wrap="wrap">
            {work.active_coordinator_run_id && (
              <Button
                size="1"
                onClick={() =>
                  useShell.getState().openInspector({
                    kind: "run",
                    objectId: work.active_coordinator_run_id!,
                    origin: { workId: work.work_id },
                  })
                }
              >
                查看执行详情
              </Button>
            )}
            {work.continuation.reason === "budget_exhausted" &&
              work.budget &&
              work.status === "active" && (
                <Button size="1" onClick={() => void increaseBudget(work)}>
                  累计预算上限翻倍
                </Button>
              )}
            {work.status === "active" && (
              <Button
                size="1"
                disabled={busy === work.work_id}
                onClick={() => void control(work, "pause")}
              >
                暂停工作
              </Button>
            )}
            {work.status === "paused" && (
              <Button
                size="1"
                disabled={busy === work.work_id}
                onClick={() => void control(work, "resume")}
              >
                恢复工作
              </Button>
            )}
            {["active", "paused", "pausing"].includes(work.status) && (
              <Button
                size="1"
                disabled={busy === work.work_id}
                onClick={() => void control(work, "stop")}
              >
                停止工作
              </Button>
            )}
            {work.status === "active" &&
              !work.active_coordinator_run_id &&
              work.continuation.kind === "blocked" && (
                <Button size="1" onClick={() => void control(work, "advance")}>
                  重试执行
                </Button>
              )}
            {conversationId && !work.conversation_ids.includes(conversationId) && (
              <Button size="1" onClick={() => void link(work, conversationId)}>
                在当前对话续接
              </Button>
            )}
          </Flex>
          {work.artifacts
            .filter((a) => a.status === "completed")
            .map((artifact) => (
              <Flex
                gap="2"
                key={`${artifact.artifact_version_id}-${artifact.role}-${artifact.source_requirement_revision}-${artifact.accepted_for_revision}`}
              >
                <Button
                  size="1"
                  variant="ghost"
                  onClick={() =>
                    void openArtifact(artifact.artifact_id, artifact.artifact_version_id)
                  }
                >
                  查看成果 · r{artifact.source_requirement_revision}
                  {artifact.accepted_for_revision === work.current_requirement_revision
                    ? " · 当前要求已采纳"
                    : artifact.accepted_for_revision
                      ? " · 历史采纳"
                      : artifact.role === "deliverable"
                        ? " · 待采纳"
                        : artifact.role === "input"
                          ? " · 输入引用"
                          : " · 证据引用"}
                </Button>
                {work.continuation.reason === "user_acceptance_required" &&
                  artifact.source_requirement_revision === work.current_requirement_revision && (
                    <Button
                      size="1"
                      onClick={() =>
                        void control(work, "accept-result", artifact.artifact_version_id)
                      }
                    >
                      接受这份成果
                    </Button>
                  )}
              </Flex>
            ))}
          {(work.workspace_saves ?? []).map((save) => (
            <p key={save.run_id}>
              工作区保存 · r{save.requirement_revision}：
              {save.state === "succeeded"
                ? "已保存"
                : save.state === "failed"
                  ? "保存失败"
                  : "等待保存"}
            </p>
          ))}
          {work.deliveries
            .filter(
              (d) => d.purpose === "fulfillment" || d.state === "failed" || d.state === "uncertain",
            )
            .slice(0, 3)
            .map((d) => (
              <div key={d.delivery_id}>
                {d.channel === "web" ? "账户收件箱" : "QQ"}：{deliveryLabels[d.state] ?? "等待投递"}
                {d.provider_receipt?.level === "account_inbox_committed" ? " · 已存入收件箱" : ""}
                {d.provider_receipt?.level === "explicitly_confirmed" ? " · 用户已确认发生" : ""}
                {d.state === "uncertain" && (
                  <Flex gap="2">
                    <Button
                      size="1"
                      onClick={() => void resolveDelivery(work, d.delivery_id, "accepted")}
                    >
                      确认已送达
                    </Button>
                    <Button
                      size="1"
                      onClick={() => void resolveDelivery(work, d.delivery_id, "not_sent")}
                    >
                      确认未发送
                    </Button>
                  </Flex>
                )}
                {["uncertain", "failed"].includes(d.state) &&
                  work.status === "active" &&
                  d.requirement_revision === work.current_requirement_revision && (
                    <Button
                      size="1"
                      onClick={() =>
                        void resolveDelivery(work, d.delivery_id, "retry_accepting_duplicate_risk")
                      }
                    >
                      接受重复风险并重试投递
                    </Button>
                  )}
              </div>
            ))}
        </article>
      ))}
    </details>
  );
}
