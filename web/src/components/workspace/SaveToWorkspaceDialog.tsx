import { TaskControlContext } from "../ui/taskControlContext";
import { TaskControlScope } from "../ui/TaskControls";
import { TaskButton, TaskInput, TaskSelect, TaskLabel, TaskForm } from "../ui/TaskControls";
import { useContext, useEffect, useRef, useState } from "react";
import { useWorkspace } from "../../store/workspace";
import { Surface } from "../shell/Surface";
import { nodePath } from "./workspacePresentation";
import {
  changeSaveTarget,
  resumeSave,
  startSave,
  useWorkspaceOperations,
  type SaveSource,
  type SaveOperation,
} from "./workspaceOperations";
export function SaveToWorkspaceDialog({
  file,
  onClose,
  onSaved,
  taskUi = false,
}: {
  taskUi?: boolean;
  file: SaveSource;
  onClose: () => void;
  onSaved: (operation: SaveOperation) => void;
}) {
  // A new source gets a new operation and view owner, including identical names.
  return (
    <TaskControlScope active={taskUi}>
      <SaveView key={JSON.stringify(file)} file={file} onClose={onClose} onSaved={onSaved} />
    </TaskControlScope>
  );
}
function SaveView({
  file,
  onClose,
  onSaved,
}: {
  file: SaveSource;
  onClose: () => void;
  onSaved: (operation: SaveOperation) => void;
}) {
  const taskUi = useContext(TaskControlContext);
  const [abandon, setAbandon] = useState(false);
  const [dirtyChoice, setDirtyChoice] = useState(false);
  const tree = useWorkspace((s) => s.tree);
  const [directory, setDirectory] = useState("");
  const [name, setName] = useState(file.file_name);
  const [id, setId] = useState<string | null>(
    () =>
      Object.values(useWorkspaceOperations.getState().operations).find(
        (op) =>
          !(op.source instanceof File) &&
          JSON.stringify(op.source) === JSON.stringify(file) &&
          !op.completed,
      )?.id ?? null,
  );
  const operationRef = useRef(id);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const operation = useWorkspaceOperations((s) => (id ? s.operations[id] : undefined));
  const generation = useWorkspace((s) => s.generation);
  const target =
    (operation?.canChangeSave ? directory : operation?.parentId) ||
    directory ||
    tree?.root_id ||
    "";
  // Query lifecycle is owned by the shared controller; this read is idempotent.
  const close = () => {
    if (dirtyChoice && (!operation || operation.canChangeSave)) setAbandon(true);
    else onClose();
  };
  return (
    <Surface
      className={
        taskUi ? "hp-object-operation hp-task-operation hp-lobe-scope" : "hp-object-operation"
      }
      motion="elevate"
      title="保存到长期文件"
      onClose={close}
    >
      <WorkspaceDirectoryLoader />
      {abandon && (
        <Surface title="放弃未提交的保存选择？" motion="elevate" onClose={() => setAbandon(false)}>
          <p>尚未提交的文件、目录或名称选择将丢弃；已提交的操作记录保留。</p>
          <TaskButton onClick={() => setAbandon(false)}>继续编辑</TaskButton>
          <TaskButton onClick={onClose}>放弃选择并关闭</TaskButton>
        </Surface>
      )}
      <TaskForm
        className="hp-operation-form"
        onDirty={() => setDirtyChoice(true)}
        onSubmit={(e) => {
          e.preventDefault();
          const previous = operationRef.current;
          const operationId = previous
            ? operation?.canChangeSave
              ? changeSaveTarget(previous, target, name.trim())
              : previous
            : startSave(file, target, name.trim());
          setDirtyChoice(false);
          operationRef.current = operationId;
          setId(operationId);
          void resumeSave(operationId).then(() => {
            if (
              alive.current &&
              useWorkspace.getState().generation === generation &&
              useWorkspaceOperations.getState().operations[operationId]?.completed
            ) {
              onSaved(useWorkspaceOperations.getState().operations[operationId]!);
              onClose();
            }
          });
        }}
      >
        <p>源文件：{file.file_name}</p>
        {"html" in file && (
          <p>以 text/plain 保存 HTML 源码文本副本（.html.txt）；交互预览仍在成果上下文。</p>
        )}
        {"html" in file && file.version && (
          <p>
            来源：{file.title} · v{file.version}
          </p>
        )}
        <p>保存不会自动授权 AI 使用。</p>
        <TaskLabel>
          保存目录
          <TaskSelect
            disabled={Boolean(id && !operation?.canChangeSave)}
            value={target}
            onChange={(e) => setDirectory(e.target.value)}
          >
            {tree?.nodes
              .filter((n) => n.kind === "directory")
              .map((n) => (
                <option key={n.node_id} value={n.node_id}>
                  {nodePath(tree, n.node_id)}
                </option>
              ))}
          </TaskSelect>
        </TaskLabel>
        <TaskLabel>
          保存名称
          <TaskInput
            required
            disabled={Boolean(id && !operation?.canChangeSave)}
            value={operation?.canChangeSave ? name : (operation?.name ?? name)}
            onChange={(e) => setName(e.target.value)}
          />
        </TaskLabel>
        {operation && (
          <p role="status">
            {operation.phase}
            {operation.busy ? "，关闭后操作仍在处理中，可从上传与保存记录查看。" : ""}
          </p>
        )}
        {operation?.error && <p role="alert">{operation.error}</p>}
        {operation?.canChangeSave && (
          <p>原保存请求被拒绝；可修改目录或名称，保留已就绪内容另存。</p>
        )}
        {!operation?.completed && (
          <TaskButton disabled={!target || !name.trim() || operation?.busy}>
            {operation?.busy ? "保存中…" : id ? "继续原保存" : "确认保存"}
          </TaskButton>
        )}
        <TaskButton type="button" onClick={close}>
          {operation?.completed ? "完成" : "关闭"}
        </TaskButton>
      </TaskForm>
    </Surface>
  );
}
export function WorkspaceDirectoryLoader() {
  const error = useWorkspace((s) => s.error);
  useEffect(() => {
    void useWorkspace
      .getState()
      .loadTree()
      .catch(() => {});
  }, []);
  return error ? (
    <p role="alert">
      {error}
      <TaskButton
        onClick={() =>
          void useWorkspace
            .getState()
            .loadTree(true)
            .catch(() => {})
        }
      >
        重试目录
      </TaskButton>
    </p>
  ) : null;
}
