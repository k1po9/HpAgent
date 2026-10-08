import { useRef, useState } from "react";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { useWorkspace } from "../../store/workspace";
import { Surface } from "../shell/Surface";
import { SubjectPicker } from "./SubjectPicker";
import { nodePath } from "./workspacePresentation";
import {
  changeSaveTarget,
  resumeSave,
  startSave,
  useWorkspaceOperations,
  type Subject,
} from "./workspaceOperations";
export function UploadToWorkspaceDialog({
  directoryId,
  onClose,
}: {
  directoryId: string;
  onClose: () => void;
}) {
  const tree = useWorkspace((s) => s.tree);
  const canUpload = useAuth((s) => s.capabilities?.file_upload === true);
  const operations = useWorkspaceOperations((s) => s.operations);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [parent, setParent] = useState(directoryId);
  const [useForConversation, setUseForConversation] = useState(false);
  const [subject, setSubject] = useState<Subject>();
  const [id, setId] = useState<string | null>(null);
  const operationRef = useRef(id);
  const operation = id ? operations[id] : undefined;
  return (
    <Surface title="上传到空间" onClose={onClose}>
      <form
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          if ((!file && !id) || !canUpload) return;
          const previous = operationRef.current;
          const operationId = previous
            ? operation?.canChangeSave
              ? changeSaveTarget(previous, parent, name.trim())
              : previous
            : startSave(file!, parent, name.trim(), useForConversation ? subject : undefined);
          operationRef.current = operationId;
          setId(operationId);
          void resumeSave(operationId);
        }}
      >
        <p>支持文本、Markdown、PDF、DOCX、XLSX、PPTX。默认仅保存到空间。</p>
        <label>
          上传长期文件
          <input
            aria-label="上传长期文件"
            type="file"
            disabled={!canUpload || Boolean(id)}
            accept=".txt,.md,.log,.pdf,.docx,.xlsx,.pptx"
            onChange={(e) => {
              const selected = e.target.files?.[0] ?? null;
              setFile(selected);
              setName(selected?.name ?? "");
            }}
          />
        </label>
        <label>
          上传目录
          <select
            disabled={Boolean(id && !operation?.canChangeSave)}
            value={operation?.canChangeSave ? parent : (operation?.parentId ?? parent)}
            onChange={(e) => setParent(e.target.value)}
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
          上传后的名称
          <input
            disabled={Boolean(id && !operation?.canChangeSave)}
            value={operation?.canChangeSave ? name : (operation?.name ?? name)}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          <input
            type="checkbox"
            disabled={Boolean(id)}
            checked={useForConversation}
            onChange={(e) => setUseForConversation(e.target.checked)}
          />
          上传后用于当前对话（显式选择目标）
        </label>
        {useForConversation && (
          <SubjectPicker
            conversationsOnly
            value={subject}
            onChange={setSubject}
            disabled={Boolean(id)}
          />
        )}
        {operation && (
          <p role="status">
            {operation.name} → {nodePath(tree, operation.parentId)}：{operation.phase}
            {operation.busy ? "；关闭后操作仍在处理中。" : ""}
          </p>
        )}
        {operation?.error && <p role="alert">{operation.error}</p>}
        {!operation?.completed && (
          <button
            disabled={
              !canUpload ||
              (!file && !id) ||
              (!id && (!name.trim() || !parent)) ||
              operation?.busy ||
              (useForConversation && !subject)
            }
          >
            {operation?.busy ? "处理中…" : id ? "继续原操作" : "上传并保存到 Workspace"}
          </button>
        )}
        {operation?.completed && (
          <button
            type="button"
            onClick={() => {
              operationRef.current = null;
              setId(null);
              setFile(null);
              setName("");
              setUseForConversation(false);
              setSubject(undefined);
            }}
          >
            上传另一个文件
          </button>
        )}
      </form>
      <details>
        <summary>本次会话上传与保存记录</summary>
        {Object.values(operations).map((op) => (
          <div key={op.id}>
            <p>
              {op.name} → {nodePath(tree, op.parentId)}：{op.phase}
              {op.error ? ` · ${op.error}` : ""}
            </p>
            {!(op.source instanceof File) && "html" in op.source && (
              <p>
                HTML 源码副本 · {op.source.title ?? op.source.file_name}
                {op.source.version ? ` · v${op.source.version}` : ""}
              </p>
            )}
            {op.nodeId && (
              <button
                onClick={() => {
                  onClose();
                  useShell.getState().navigate({
                    screen: "workspace",
                    directoryId: op.parentId,
                    inspector: { kind: "file", objectId: op.nodeId! },
                  });
                }}
              >
                打开空间
              </button>
            )}
            {!op.busy && !op.completed && (
              <button
                onClick={() => {
                  operationRef.current = op.id;
                  setId(op.id);
                  setName(op.name);
                  setParent(op.parentId);
                  setUseForConversation(Boolean(op.subject));
                  setSubject(op.subject);
                  void resumeSave(op.id);
                }}
              >
                继续此操作
              </button>
            )}
          </div>
        ))}
      </details>
    </Surface>
  );
}
