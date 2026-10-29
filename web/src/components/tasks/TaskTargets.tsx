import { TaskButton, TaskInput, TaskLabel, TaskForm } from "../ui/TaskControls";
import { validSnapshot } from "./taskActions";
import { useState } from "react";
import type { HpWork } from "../../api/types";
import { useWorks } from "../../store/works";
import { useTaskQuery } from "./useTaskQuery";
import { useTaskOperations } from "./taskOperations";
import { Surface } from "../shell/Surface";
export function TaskTargets({ work }: { work: HpWork }) {
  const query = useTaskQuery<{
    items: Array<{
      target_id: string;
      channel: string;
      enabled: boolean;
      audience: string;
      content_scope: string;
      target_version: number;
    }>;
  }>(
    `targets:${work.work_id}:${work.row_version}`,
    `/api/v1/works/${work.work_id}/notification-targets`,
  );
  const [target, setTarget] = useState<string | null>(null),
    [source, setSource] = useState("");
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const active = validSnapshot(work) && !["stopped", "completed"].includes(work.status);
  return (
    <section>
      <h4>通知目标</h4>
      {query.error && (
        <p role="alert">
          通知目标查询失败。<TaskButton onClick={query.retry}>重试目标</TaskButton>
        </p>
      )}
      {query.data?.items.map((t) => (
        <p key={t.target_id}>
          {t.channel === "web" ? "账户收件箱" : t.channel === "qq" ? "QQ" : "未知渠道"} ·{" "}
          {t.audience} · {t.enabled ? "已启用" : "已停用"} · v{t.target_version}
          {t.enabled && ["web", "qq"].includes(t.channel) && (
            <TaskButton
              disabled={intent?.busy || intent?.uncertain}
              onClick={() => setTarget(t.target_id)}
            >
              停用此通知目标
            </TaskButton>
          )}
        </p>
      ))}
      {active && (
        <>
          <TaskButton
            disabled={intent?.busy || intent?.uncertain}
            onClick={() =>
              void useWorks.getState().command(work, {
                method: "PUT",
                path: `/api/v1/works/${work.work_id}/notification-targets`,
                body: { content_scope: "summary" },
              })
            }
          >
            启用账户收件箱目标
          </TaskButton>
          <TaskForm
            onSubmit={(e) => {
              e.preventDefault();
              void useWorks.getState().command(work, {
                method: "PUT",
                path: `/api/v1/works/${work.work_id}/notification-targets`,
                body: { source_message_id: source.trim(), content_scope: "summary" },
              });
            }}
          >
            <TaskLabel>
              已验证来源消息编号
              <TaskInput required value={source} onChange={(e) => setSource(e.target.value)} />
            </TaskLabel>
            <p>渠道目标从有权访问的消息来源解析；群聊只发送摘要。</p>
            <TaskButton disabled={intent?.busy || intent?.uncertain}>
              从来源消息设置通知目标
            </TaskButton>
          </TaskForm>
        </>
      )}
      {intent?.error && <p role="alert">{intent.error}</p>}
      {target && (
        <Surface
          className="hp-object-operation hp-task-operation hp-lobe-scope"
          motion="elevate"
          title="停用通知目标"
          onClose={() => setTarget(null)}
        >
          <p>将停止向此目标投递，待发送或失败的通知会撤销。已经发送或结果未决的通知仍需核查。</p>
          <TaskButton
            disabled={intent?.busy}
            onClick={() =>
              void useWorks
                .getState()
                .command(work, {
                  method: "DELETE",
                  path: `/api/v1/works/${work.work_id}/notification-targets/${target}`,
                })
                .then((ok) => {
                  if (ok) setTarget(null);
                })
            }
          >
            确认停用此目标
          </TaskButton>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
    </section>
  );
}
