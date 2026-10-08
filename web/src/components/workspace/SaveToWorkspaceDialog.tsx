import { useEffect, useRef, useState } from "react";
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
}: {
  file: SaveSource;
  onClose: () => void;
  onSaved: (operation: SaveOperation) => void;
}) {
  // A new source gets a new operation and view owner, including identical names.
  return <SaveView key={JSON.stringify(file)} file={file} onClose={onClose} onSaved={onSaved} />;
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
  return (
    <Surface title="保存到长期文件" onClose={onClose}>
      <WorkspaceDirectoryLoader />
      <form
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          const previous = operationRef.current;
          const operationId = previous
            ? operation?.canChangeSave
              ? changeSaveTarget(previous, target, name.trim())
              : previous
            : startSave(file, target, name.trim());
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
        <label>
          保存目录
          <select
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
          </select>
        </label>
        <label>
          保存名称
          <input
            required
            disabled={Boolean(id && !operation?.canChangeSave)}
            value={operation?.canChangeSave ? name : (operation?.name ?? name)}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
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
          <button disabled={!target || !name.trim() || operation?.busy}>
            {operation?.busy ? "保存中…" : id ? "继续原保存" : "确认保存"}
          </button>
        )}
        <button type="button" onClick={onClose}>
          {operation?.completed ? "完成" : "关闭"}
        </button>
      </form>
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
      <button
        onClick={() =>
          void useWorkspace
            .getState()
            .loadTree(true)
            .catch(() => {})
        }
      >
        重试目录
      </button>
    </p>
  ) : null;
}
