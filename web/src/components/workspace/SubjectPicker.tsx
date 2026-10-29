import { WorkspaceSelectField } from "./WorkspaceControls";
import { useEffect, useState } from "react";
import { useWorkbench } from "../../store/workbench";
import { useWorks } from "../../store/works";
import { api } from "../../api/client";
import { HpCommandError, type HpWork } from "../../api/types";
import { useShell } from "../../store/shell";
import { useWorkspace } from "../../store/workspace";
import type { Subject } from "./workspaceOperations";
export function SubjectPicker({
  value,
  onChange,
  conversationsOnly = false,
  disabled = false,
  workspaceUi = false,
}: {
  value?: Subject;
  onChange: (s: Subject | undefined) => void;
  conversationsOnly?: boolean;
  disabled?: boolean;
  workspaceUi?: boolean;
}) {
  const conversations = useWorkbench((s) => s.conversations);
  const hasMore = useWorkbench((s) => s.hasMoreConversations);
  const loadingMore = useWorkbench((s) => s.loadingMoreConversations);
  const works = useWorks((s) => s.items);
  const [kind, setKind] = useState<"conversation" | "work">(value?.kind ?? "conversation");
  const [workId, setWorkId] = useState("");
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    void useWorkbench.getState().loadConversations();
  }, []);
  const options =
    kind === "conversation"
      ? conversations.map((item) => ({ value: item.conversation_id, label: item.title }))
      : works.map((item) => ({ value: item.work_id, label: item.title }));
  if (value?.kind === kind && !options.some((option) => option.value === value.id))
    options.push({ value: value.id, label: value.title });
  const selectSubject = (id: string) => {
    const title = options.find((option) => option.value === id)?.label ?? id;
    useShell.getState().requestResourceChange(() => onChange(id ? { kind, id, title } : undefined));
  };
  return (
    <div className="hp-subject-picker">
      {!conversationsOnly && (
        <label>
          授权主体
          <select
            disabled={disabled || checking}
            value={kind}
            onChange={(e) => {
              const next = e.target.value as typeof kind;
              useShell.getState().requestResourceChange(() => {
                setKind(next);
                onChange(undefined);
              });
            }}
          >
            <option value="conversation">对话</option>
            <option value="work">任务</option>
          </select>
        </label>
      )}
      {workspaceUi ? (
        <WorkspaceSelectField
          label={kind === "conversation" ? "目标对话" : "已加载任务"}
          disabled={disabled || checking}
          value={value?.kind === kind ? value.id : ""}
          options={[{ value: "", label: "请选择" }, ...options]}
          onChange={selectSubject}
        />
      ) : (
        <label>
          {kind === "conversation" ? "目标对话" : "已加载任务"}
          <select
            disabled={disabled || checking}
            value={value?.kind === kind ? value.id : ""}
            onChange={(event) => selectSubject(event.target.value)}
          >
            <option value="">请选择</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {kind === "conversation" && hasMore && (
        <button
          type="button"
          disabled={disabled || loadingMore}
          onClick={() => void useWorkbench.getState().loadMoreConversations()}
        >
          加载更多对话
        </button>
      )}
      {kind === "work" && (
        <>
          <p>此列表仅为已加载任务，可用编号验证其他任务。</p>
          <label>
            任务编号
            <input
              disabled={disabled || checking}
              value={workId}
              onChange={(e) => setWorkId(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={disabled || checking || !workId.trim()}
            onClick={() => {
              setChecking(true);
              setError("");
              const generation = useWorkspace.getState().generation;
              void api
                .request<{ work: HpWork }>({
                  method: "GET",
                  path: `/api/v1/works/${encodeURIComponent(workId.trim())}`,
                })
                .then(({ work }) => {
                  if (generation === useWorkspace.getState().generation)
                    useShell
                      .getState()
                      .requestResourceChange(() =>
                        onChange({ kind: "work", id: work.work_id, title: work.title }),
                      );
                })
                .catch((e) => {
                  if (generation === useWorkspace.getState().generation)
                    setError(
                      e instanceof HpCommandError && [403, 404].includes(e.status)
                        ? "对象不可用。"
                        : "任务验证失败。",
                    );
                })
                .finally(() => {
                  if (generation === useWorkspace.getState().generation) setChecking(false);
                });
            }}
          >
            验证任务
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
