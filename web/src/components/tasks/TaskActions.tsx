import { ActionMenu } from "../ui/ActionMenu";
import { Button, Field, Select } from "../ui";
import { useEffect, useRef, useState } from "react";
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
  const draftOwner = useShell((s) => s.dirtyTaskEditor);
  const ownedDraft = useRef(false);
  useEffect(() => {
    if (dialog === "edit" && draftOwner === work.work_id) ownedDraft.current = true;
    else if (ownedDraft.current && !draftOwner) {
      // Approved navigation released this draft; a retained tab must not revive its editor.
      ownedDraft.current = false;
      setDialog(null);
    }
  }, [dialog, draftOwner, work.work_id]);
  const conversations = useWorkbench((s) => s.conversations);
  const more = useWorkbench((s) => s.hasMoreConversations);
  const loading = useWorkbench((s) => s.loadingMoreConversations);
  const intent = useTaskOperations((s) => s.intents[work.work_id]);
  const all = taskActions(work, now);
  const primary = presentTask(work, now).primaryAction;
  const primaryButton = useRef<HTMLButtonElement>(null);
  const restorePrimaryFocus = useRef(false);
  useEffect(() => {
    if (intent?.busy || !restorePrimaryFocus.current) return;
    if (document.activeElement === document.body) primaryButton.current?.focus();
    restorePrimaryFocus.current = false;
  }, [intent?.busy, primary]);
  function act(action: TaskAction, triggerId?: string) {
    if (["view", "outputs", "resources"].includes(action)) {
      const current = useShell.getState().route.inspector;
      useShell.getState().openInspector({
        kind: "task",
        objectId: work.work_id,
        origin:
          current?.kind === "task" && current.objectId === work.work_id
            ? current.origin
            : {
                triggerId:
                  triggerId ??
                  `task-action-${compact ? "row" : "inspector"}-${work.work_id}-${action}`,
                workId: work.work_id,
                sourceRequirementRevision: work.current_requirement_revision,
              },
        tab: action === "view" ? "overview" : action === "outputs" ? "outputs" : "resources",
      });
    } else if (["stop", "budget", "edit"].includes(action)) setDialog(action);
    else void useWorks.getState().control(work, action as "pause" | "resume" | "advance");
  }
  const button = (action: TaskAction) => (
    <Button
      type="button"
      key={compact && action === primary ? "primary" : action}
      ref={compact && action === primary ? primaryButton : undefined}
      className={compact && action === primary ? "hp-task-primary-action" : undefined}
      id={`task-action-${compact ? "row" : "inspector"}-${work.work_id}-${action}`}
      variant={action === "advance" || action === "resume" ? "solid" : "outline"}
      color={action === "stop" ? "red" : undefined}
      data-action={action}
      disabled={
        intent?.busy || (intent?.uncertain && !["view", "outputs", "resources"].includes(action))
      }
      onClick={(event) => {
        if (compact && action === primary && ["pause", "resume", "advance"].includes(action)) {
          restorePrimaryFocus.current = document.activeElement === event.currentTarget;
        }
        act(action);
      }}
    >
      {actionLabels[action]}
    </Button>
  );
  return (
    <div className="hp-task-actions">
      {compact ? (
        <>
          {button(primary)}
          <ActionMenu
            id={`task-more-${work.work_id}`}
            items={[
              ...all
                .filter((a) => a !== primary)
                .map((action) => ({
                  id: `task-action-row-${work.work_id}-${action}`,
                  label: actionLabels[action],
                  disabled: Boolean(
                    intent?.busy ||
                    (intent?.uncertain && !["view", "outputs", "resources"].includes(action)),
                  ),
                  onSelect: () => act(action, `task-more-${work.work_id}`),
                })),
              {
                id: `task-link-${work.work_id}`,
                label: "关联对话",
                disabled: Boolean(intent?.busy || intent?.uncertain || !validSnapshot(work)),
                onSelect: () => setDialog("link"),
              },
            ]}
          />
        </>
      ) : (
        <>
          {all.map(button)}
          <Button
            type="button"
            disabled={intent?.busy || intent?.uncertain || !validSnapshot(work)}
            onClick={() => setDialog("link")}
          >
            关联对话
          </Button>
        </>
      )}
      {intent?.error && <p role="alert">{intent.error}</p>}
      {intent?.uncertain && (
        <Button
          type="button"
          disabled={intent.busy}
          onClick={() =>
            void retryTaskCommand(work.work_id, async (snapshot) => {
              if (snapshot) useWorks.getState().upsert(snapshot);
              else await useWorks.getState().refresh(work.work_id);
            })
          }
        >
          重试原操作
        </Button>
      )}
      {intent?.notice && <p role="status">{intent.notice}</p>}
      {dialog === "budget" && work.budget && (
        <TaskBudgetDialog work={work} onClose={() => setDialog(null)} />
      )}
      {dialog === "edit" && <TaskEditor workId={work.work_id} onClose={() => setDialog(null)} />}
      {dialog === "stop" && (
        <Surface
          className="hp-object-operation"
          motion="elevate"
          title="停止任务"
          onClose={() => setDialog(null)}
        >
          <p>
            停止「{work.title}」后不再按原计划继续。未决外部操作仍需核查，任务可能暂时显示正在停止。
          </p>
          <Button
            type="button"
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
          </Button>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
      {dialog === "link" && (
        <Surface
          className="hp-object-operation"
          motion="elevate"
          title="关联任务到对话"
          onClose={() => setDialog(null)}
        >
          <p>只关联任务，不复制权限或发送消息。</p>
          <Field>
            目标对话
            <Select value={conversation} onChange={(e) => setConversation(e.target.value)}>
              <option value="">请选择</option>
              {conversations
                .filter((c) => !work.conversation_ids.includes(c.conversation_id))
                .map((c) => (
                  <option key={c.conversation_id} value={c.conversation_id}>
                    {c.title}
                  </option>
                ))}
            </Select>
          </Field>
          {more && (
            <Button
              type="button"
              disabled={loading}
              onClick={() => void useWorkbench.getState().loadMoreConversations()}
            >
              加载更多对话
            </Button>
          )}
          <Button
            type="button"
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
          </Button>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
    </div>
  );
}
