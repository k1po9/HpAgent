import { useState } from "react";
import type { HpWork } from "../../api/types";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { useWorkbench } from "../../store/workbench";
import { Surface } from "../shell/Surface";
import { actionLabels, taskActions, validSnapshot, type TaskAction } from "./taskActions";
import { presentTask } from "./taskPresentation";
import { TaskBudgetDialog } from "./TaskBudgetDialog";
import { TaskEditor } from "./TaskEditor";
import { useTaskOperations, retryTaskCommand } from "./taskOperations";
export function TaskActions({ work, compact = false }: { work: HpWork; compact?: boolean }) {
  const now = useWorks((s) => s.now);
  const [dialog, setDialog] = useState<TaskAction | "link" | null>(null);
  const [conversation, setConversation] = useState("");
  const conversations = useWorkbench((s) => s.conversations);
  const more = useWorkbench((s) => s.hasMoreConversations);
  const loading = useWorkbench((s) => s.loadingMoreConversations);
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const all = taskActions(work, now);
  const primary = presentTask(work, now).primaryAction;
  function act(action: TaskAction) {
    if (["view", "outputs", "resources"].includes(action)) {
      useShell.getState().openInspector({
        kind: "task",
        objectId: work.work_id,
        tab: action === "view" ? "overview" : action === "outputs" ? "outputs" : "resources",
      });
    } else if (["stop", "budget", "edit"].includes(action)) setDialog(action);
    else void useWorks.getState().control(work, action as "pause" | "resume" | "advance");
  }
  const button = (action: TaskAction) => (
    <button
      key={action}
      disabled={
        intent?.busy || (intent?.uncertain && !["view", "outputs", "resources"].includes(action))
      }
      onClick={() => act(action)}
    >
      {actionLabels[action]}
    </button>
  );
  return (
    <div className="hp-task-actions">
      {compact ? (
        <>
          {button(primary)}
          <details>
            <summary>更多</summary>
            <div>
              {all.filter((a) => a !== primary).map(button)}
              <button
                disabled={intent?.busy || intent?.uncertain || !validSnapshot(work)}
                onClick={() => setDialog("link")}
              >
                关联对话
              </button>
            </div>
          </details>
        </>
      ) : (
        <>
          {all.map(button)}
          <button
            disabled={intent?.busy || intent?.uncertain || !validSnapshot(work)}
            onClick={() => setDialog("link")}
          >
            关联对话
          </button>
        </>
      )}
      {intent?.error && <p role="alert">{intent.error}</p>}
      {intent?.uncertain && (
        <button
          disabled={intent.busy}
          onClick={() =>
            void retryTaskCommand(work.work_id, async (snapshot) => {
              if (snapshot) useWorks.getState().upsert(snapshot);
              else await useWorks.getState().refresh(work.work_id);
            })
          }
        >
          重试原操作
        </button>
      )}
      {intent?.notice && <p role="status">{intent.notice}</p>}
      {dialog === "budget" && work.budget && (
        <TaskBudgetDialog work={work} onClose={() => setDialog(null)} />
      )}
      {dialog === "edit" && <TaskEditor workId={work.work_id} onClose={() => setDialog(null)} />}
      {dialog === "stop" && (
        <Surface title="停止任务" onClose={() => setDialog(null)}>
          <p>
            停止「{work.title}」后不再按原计划继续。未决外部操作仍需核查，任务可能暂时显示正在停止。
          </p>
          <button
            disabled={intent?.busy}
            onClick={() =>
              void useWorks
                .getState()
                .control(work, "stop")
                .then((ok) => {
                  if (ok) setDialog(null);
                })
            }
          >
            确认停止任务
          </button>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
      {dialog === "link" && (
        <Surface title="关联任务到对话" onClose={() => setDialog(null)}>
          <p>只关联任务，不复制权限或发送消息。</p>
          <label>
            目标对话
            <select value={conversation} onChange={(e) => setConversation(e.target.value)}>
              <option value="">请选择</option>
              {conversations
                .filter((c) => !work.conversation_ids.includes(c.conversation_id))
                .map((c) => (
                  <option key={c.conversation_id} value={c.conversation_id}>
                    {c.title}
                  </option>
                ))}
            </select>
          </label>
          {more && (
            <button
              disabled={loading}
              onClick={() => void useWorkbench.getState().loadMoreConversations()}
            >
              加载更多对话
            </button>
          )}
          <button
            disabled={!conversation || intent?.busy}
            onClick={() =>
              void useWorks
                .getState()
                .link(work, conversation)
                .then((ok) => {
                  if (ok) setDialog(null);
                })
            }
          >
            确认关联
          </button>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
    </div>
  );
}
