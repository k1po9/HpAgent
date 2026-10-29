import { Alert, Button, Checkbox, Input, Upload } from "@lobehub/ui";
import { WorkspaceDisclosure, WorkspaceSelectField } from "./WorkspaceControls";
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
  const [abandon, setAbandon] = useState(false);
  const [dirtyChoice, setDirtyChoice] = useState(false);
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
  const close = () => {
    if (dirtyChoice && (!operation || operation.canChangeSave)) setAbandon(true);
    else onClose();
  };
  return (
    <Surface
      className="hp-workspace-upload hp-lobe-scope"
      motion="elevate"
      title="上传到空间"
      onClose={close}
    >
      {abandon && (
        <Surface title="放弃未提交的上传选择？" motion="elevate" onClose={() => setAbandon(false)}>
          <p>尚未提交的文件、目录或名称选择将丢弃；已提交的操作记录保留。</p>
          <Button onClick={() => setAbandon(false)}>继续编辑</Button>
          <Button onClick={onClose}>放弃选择并关闭</Button>
        </Surface>
      )}
      <form
        className="hp-operation-form"
        onChange={() => setDirtyChoice(true)}
        onSubmit={(e) => {
          e.preventDefault();
          if ((!file && !id) || !canUpload) return;
          const previous = operationRef.current;
          const operationId = previous
            ? operation?.canChangeSave
              ? changeSaveTarget(previous, parent, name.trim())
              : previous
            : startSave(file!, parent, name.trim(), useForConversation ? subject : undefined);
          setDirtyChoice(false);
          operationRef.current = operationId;
          setId(operationId);
          void resumeSave(operationId);
        }}
      >
        <p>支持文本、Markdown、PDF、DOCX、XLSX、PPTX。默认仅保存到空间。</p>
        <div className="hp-workspace-field">
          <span id="workspace-upload-label">上传长期文件</span>
          <Upload
            dragger
            aria-labelledby="workspace-upload-label"
            disabled={!canUpload || Boolean(id)}
            accept=".txt,.md,.log,.pdf,.docx,.xlsx,.pptx"
            maxCount={1}
            title={file?.name ?? "点击选择或拖入文件"}
            description="TXT / Markdown / PDF / DOCX / XLSX / PPTX"
            onFiles={([selected]) => {
              if (!selected) return;
              setFile(selected);
              setName(selected.name);
              setDirtyChoice(true);
            }}
          />
        </div>
        <WorkspaceSelectField
          label="上传目录"
          disabled={Boolean(id && !operation?.canChangeSave)}
          value={operation?.canChangeSave ? parent : (operation?.parentId ?? parent)}
          options={
            tree?.nodes
              .filter((node) => node.kind === "directory")
              .map((node) => ({ value: node.node_id, label: nodePath(tree, node.node_id) })) ?? []
          }
          onChange={(value) => {
            setParent(value);
            setDirtyChoice(true);
          }}
        />
        <label>
          上传后的名称
          <Input
            disabled={Boolean(id && !operation?.canChangeSave)}
            value={operation?.canChangeSave ? name : (operation?.name ?? name)}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <Checkbox
          disabled={Boolean(id)}
          checked={useForConversation}
          onChange={(checked) => {
            setUseForConversation(checked);
            setDirtyChoice(true);
          }}
        >
          上传后用于当前对话（显式选择目标）
        </Checkbox>
        {useForConversation && (
          <SubjectPicker
            conversationsOnly
            workspaceUi
            value={subject}
            onChange={(value) => {
              setSubject(value);
              setDirtyChoice(true);
            }}
            disabled={Boolean(id)}
          />
        )}
        {operation && (
          <p role="status">
            {operation.name} → {nodePath(tree, operation.parentId)}：{operation.phase}
            {operation.busy ? "；关闭后操作仍在处理中。" : ""}
          </p>
        )}
        {operation?.error && <Alert type="error" title={operation.error} />}
        {!operation?.completed && (
          <Button
            type="primary"
            htmlType="submit"
            loading={operation?.busy}
            disabled={
              !canUpload ||
              (!file && !id) ||
              (!id && (!name.trim() || !parent)) ||
              operation?.busy ||
              (useForConversation && !subject)
            }
          >
            {operation?.busy ? "处理中…" : id ? "继续原操作" : "上传并保存到 Workspace"}
          </Button>
        )}
        {operation?.completed && (
          <Button
            htmlType="button"
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
          </Button>
        )}
      </form>
      <WorkspaceDisclosure title="本次会话上传与保存记录" className="hp-workspace-upload-history">
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
              <Button
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
              </Button>
            )}
            {!op.busy && !op.completed && (
              <Button
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
              </Button>
            )}
          </div>
        ))}
      </WorkspaceDisclosure>
    </Surface>
  );
}
