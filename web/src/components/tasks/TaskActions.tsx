import { TaskButton, TaskSelect, TaskLabel } from "../ui/TaskControls";
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
import { DropdownMenu } from "@lobehub/ui";
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
  const moreButton = useRef<HTMLButtonElement>(null);
  const openingDialog = useRef(false);
  const pendingDialog = useRef<TaskAction | "link" | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = `task-more-${compact ? "row" : "inspector"}-${work.work_id}`;
  const blocked = (action: TaskAction) =>
    Boolean(
      intent?.busy || (intent?.uncertain && !["view", "outputs", "resources"].includes(action)),
    );
  useEffect(() => {
    if (!menuOpen) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Autofocus may still be on the Inspector title immediately after opening.
      // Claim this Escape before either Base UI or the native Surface consumes it.
      event.preventDefault();
      event.stopPropagation();
      setMenuOpen(false);
      queueMicrotask(() => moreButton.current?.focus());
    };
    window.addEventListener("keydown", dismiss, true);
    return () => window.removeEventListener("keydown", dismiss, true);
  }, [menuOpen]);
  useEffect(() => {
    if (dialog || !openingDialog.current) return;
    openingDialog.current = false;
    queueMicrotask(() => {
      const trigger = moreButton.current;
      if (trigger?.isConnected && !trigger.closest("[hidden], [inert]")) trigger.focus();
    });
  }, [dialog]);
  useEffect(() => {
    if (intent?.busy || !restorePrimaryFocus.current) return;
    if (document.activeElement === document.body) primaryButton.current?.focus();
    restorePrimaryFocus.current = false;
  }, [intent?.busy, primary]);
  function act(action: TaskAction, fromMenu = false) {
    if (blocked(action)) return;
    if (["view", "outputs", "resources"].includes(action)) {
      const current = useShell.getState().route.inspector;
      useShell.getState().openInspector({
        kind: "task",
        objectId: work.work_id,
        origin:
          current?.kind === "task" && current.objectId === work.work_id
            ? current.origin
            : {
                triggerId: fromMenu
                  ? menuId
                  : `task-action-${compact ? "row" : "inspector"}-${work.work_id}-${action}`,
                workId: work.work_id,
                sourceRequirementRevision: work.current_requirement_revision,
              },
        tab: action === "view" ? "overview" : action === "outputs" ? "outputs" : "resources",
      });
    } else if (["stop", "budget", "edit"].includes(action)) setDialog(action);
    else void useWorks.getState().control(work, action as "pause" | "resume" | "advance");
  }
  const button = (action: TaskAction) => (
    <TaskButton
      key={compact && action === primary ? "primary" : action}
      ref={compact && action === primary ? primaryButton : undefined}
      className={compact && action === primary ? "hp-task-primary-action" : undefined}
      appearance={
        compact && action === primary
          ? presentTask(work, now).bucket === "attention"
            ? "default"
            : "primary"
          : undefined
      }
      loading={compact && action === primary && Boolean(intent?.busy)}
      id={`task-action-${compact ? "row" : "inspector"}-${work.work_id}-${action}`}
      data-action={action}
      disabled={blocked(action)}
      onClick={(event) => {
        if (compact && action === primary && ["pause", "resume", "advance"].includes(action)) {
          restorePrimaryFocus.current = document.activeElement === event.currentTarget;
        }
        act(action);
      }}
    >
      {actionLabels[action]}
    </TaskButton>
  );
  const secondary = compact
    ? all.filter((a) => a !== primary)
    : all.filter((a) => !["pause", "resume", "advance", "edit", "budget"].includes(a));
  const menu = (
    <DropdownMenu
      nativeButton
      open={menuOpen}
      onOpenChange={setMenuOpen}
      triggerProps={{ id: menuId, disabled: Boolean(intent?.busy) }}
      onOpenChangeComplete={(open) => {
        if (open || !pendingDialog.current) return;
        const next = pendingDialog.current;
        pendingDialog.current = null;
        // Complete the popup's focus/portal cleanup before opening a native dialog.
        moreButton.current?.focus();
        if (next === "link" || !blocked(next)) setDialog(next);
      }}
      popupProps={{
        finalFocus: () => (openingDialog.current ? false : moreButton.current),
        onKeyDown: (event) => {
          if (event.key !== "Escape") return;
          // Native/non-modal Surface Escape must not also consume the menu dismissal.
          event.preventDefault();
          event.stopPropagation();
          setMenuOpen(false);
        },
      }}
      items={[
        ...secondary.map((action) => ({
          key: action,
          label: actionLabels[action],
          danger: action === "stop",
          disabled: blocked(action),
          onClick: () => {
            if (blocked(action)) return;
            openingDialog.current = ["edit", "budget", "stop"].includes(action);
            if (openingDialog.current) {
              pendingDialog.current = action;
              setMenuOpen(false);
            } else act(action, true);
          },
        })),
        ...(compact
          ? [
              {
                key: "link",
                label: "关联对话",
                disabled: Boolean(intent?.busy || intent?.uncertain || !validSnapshot(work)),
                onClick: () => {
                  openingDialog.current = true;
                  pendingDialog.current = "link";
                  setMenuOpen(false);
                },
              },
            ]
          : []),
      ]}
    >
      <TaskButton
        type="button"
        appearance={compact ? "text" : "default"}
        id={menuId}
        ref={moreButton}
        disabled={intent?.busy}
      >
        {compact ? "更多" : "更多操作"}
      </TaskButton>
    </DropdownMenu>
  );
  return (
    <div
      className="hp-task-actions hp-task-actions--lobe"
      onKeyDownCapture={(event) => {
        if (event.key !== "Escape" || !menuOpen) return;
        // Includes Escape pressed on the trigger before popup autofocus completes.
        event.preventDefault();
        event.stopPropagation();
        setMenuOpen(false);
      }}
    >
      {compact ? (
        <>
          {button(primary)}
          {menu}
        </>
      ) : (
        <>
          {all
            .filter((a) => ["pause", "resume", "advance", "edit", "budget"].includes(a))
            .map(button)}
          {menu}
          <TaskButton
            disabled={intent?.busy || intent?.uncertain || !validSnapshot(work)}
            onClick={() => setDialog("link")}
          >
            关联对话
          </TaskButton>
        </>
      )}
      {intent?.error && <p role="alert">{intent.error}</p>}
      {intent?.uncertain && (
        <TaskButton
          disabled={intent.busy}
          onClick={() =>
            void retryTaskCommand(work.work_id, async (snapshot) => {
              if (snapshot) useWorks.getState().upsert(snapshot);
              else await useWorks.getState().refresh(work.work_id);
            })
          }
        >
          重试原操作
        </TaskButton>
      )}
      {intent?.notice && <p role="status">{intent.notice}</p>}
      {dialog === "budget" && work.budget && (
        <TaskBudgetDialog work={work} onClose={() => setDialog(null)} />
      )}
      {dialog === "edit" && <TaskEditor workId={work.work_id} onClose={() => setDialog(null)} />}
      {dialog === "stop" && (
        <Surface
          className="hp-object-operation hp-task-operation hp-lobe-scope"
          motion="elevate"
          title="停止任务"
          onClose={() => setDialog(null)}
        >
          <p>
            停止「{work.title}」后不再按原计划继续。未决外部操作仍需核查，任务可能暂时显示正在停止。
          </p>
          <TaskButton
            danger
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
          </TaskButton>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
      {dialog === "link" && (
        <Surface
          className="hp-object-operation hp-task-operation hp-lobe-scope"
          motion="elevate"
          title="关联任务到对话"
          onClose={() => setDialog(null)}
        >
          <p>只关联任务，不复制权限或发送消息。</p>
          <TaskLabel>
            目标对话
            <TaskSelect value={conversation} onChange={(e) => setConversation(e.target.value)}>
              <option value="">请选择</option>
              {conversations
                .filter((c) => !work.conversation_ids.includes(c.conversation_id))
                .map((c) => (
                  <option key={c.conversation_id} value={c.conversation_id}>
                    {c.title}
                  </option>
                ))}
            </TaskSelect>
          </TaskLabel>
          {more && (
            <TaskButton
              disabled={loading}
              onClick={() => void useWorkbench.getState().loadMoreConversations()}
            >
              加载更多对话
            </TaskButton>
          )}
          <TaskButton
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
          </TaskButton>
          {intent?.error && <p role="alert">{intent.error}</p>}
        </Surface>
      )}
    </div>
  );
}
