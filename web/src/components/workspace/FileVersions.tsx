import { useState } from "react";
import type { HpWorkspaceNode } from "../../api/types";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useWorkspaceQuery } from "./useWorkspaceQuery";
import { commandError } from "../../utils/commands";
import { SaveToWorkspaceDialog } from "./SaveToWorkspaceDialog";
import {
  useVersionOperations,
  startVersionUpdate,
  resumeVersionUpdate,
  clearRejectedVersionUpdate,
} from "./versionOperations";
export function FileVersions({ node }: { node: HpWorkspaceNode }) {
  const query = useWorkspaceQuery(`versions:${node.node_id}`, () =>
    workspaceApi.getWorkspaceVersions(node.node_id),
  );
  const generation = useWorkspace((s) => s.generation);
  const operation = useVersionOperations((s) =>
    s.operations[node.node_id]?.generation === generation ? s.operations[node.node_id] : undefined,
  );
  const [runId, setRunId] = useState(operation?.runId ?? "");
  const [appliedRun, setAppliedRun] = useState(operation?.runId ?? "");
  const published = useWorkspaceQuery(appliedRun ? `published:${appliedRun}` : null, () =>
    workspaceApi.listRunPublishedFiles(appliedRun),
  );
  const [fileId, setFileId] = useState(operation?.fileId ?? "");
  const [upgrading, setUpgrading] = useState(false);
  const unresolved = operation?.state === "pending" || operation?.state === "unknown";
  const busy = upgrading || Boolean(operation?.busy);
  const locked = busy || unresolved;
  const [error, setError] = useState("");
  const conflict = operation?.conflict === true;
  const [save, setSave] = useState(false);
  const current = query.data?.current;
  const selected =
    published.data?.files.find((f) => f.file_id === fileId) ??
    (operation && operation.fileId === fileId
      ? { file_id: operation.fileId, name: operation.fileName }
      : undefined);
  return (
    <section className="hp-operation-form" aria-label="Workspace 版本">
      <p>当前版本：{current?.revision ?? "不可变入口"}；历史文件只读。</p>
      {query.loading && <p role="status">正在加载版本…</p>}
      {query.error && (
        <p role="alert">
          {query.error}
          <button onClick={query.retry}>重试版本历史</button>
        </p>
      )}
      {query.data && !current?.destination_id && (
        <button
          disabled={locked}
          onClick={() => {
            setUpgrading(true);
            void workspaceApi
              .upgradeWorkspaceFile(node.node_id)
              .then(() => {
                if (generation === useWorkspace.getState().generation)
                  useWorkspace.getState().invalidate();
              })
              .catch((e) => {
                if (generation === useWorkspace.getState().generation) setError(commandError(e));
              })
              .finally(() => {
                if (generation === useWorkspace.getState().generation) setUpgrading(false);
              });
          }}
        >
          启用版本历史
        </button>
      )}
      {query.data?.revisions.map((version) => (
        <p key={version.revision}>
          v{version.revision} · 版本生成时间 {version.created_at} ·{" "}
          {version.source.purpose === "output" ? "AI 输出" : "上传"}{" "}
          <a href={`/api/v1/files/${encodeURIComponent(version.file_id)}/content`} download>
            下载历史版本 {version.revision}
          </a>
        </p>
      ))}
      {current?.destination_id && (
        <>
          <label>
            产生新版本的执行编号
            <input
              aria-label="编辑 Run ID"
              disabled={locked}
              value={runId}
              onChange={(e) => {
                setRunId(e.target.value);
                setAppliedRun("");
                setFileId("");
                clearRejectedVersionUpdate(node.node_id);
              }}
            />
          </label>
          <button
            disabled={locked || !runId.trim()}
            onClick={() => {
              setAppliedRun(runId.trim());
              setFileId("");
            }}
          >
            选择 Run 已发布输出
          </button>
          {published.loading && <p role="status">正在加载执行输出…</p>}
          {published.error && (
            <p role="alert">
              {published.error}
              <button onClick={published.retry}>重试输出查询</button>
            </p>
          )}
          {published.data && (
            <label>
              Run 已发布输出
              <select
                aria-label="Run 已发布输出"
                disabled={locked}
                value={fileId}
                onChange={(e) => {
                  setFileId(e.target.value);
                  clearRejectedVersionUpdate(node.node_id);
                }}
              >
                <option value="">请选择</option>
                {published.data.files.map((f) => (
                  <option key={f.file_id} value={f.file_id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {published.data?.files.length === 0 && <p>此执行暂无已发布输出。</p>}
          <button
            disabled={locked || query.loading || !selected || current.revision === null || conflict}
            onClick={() => {
              if (!selected || current.revision === null) return;
              startVersionUpdate({
                nodeId: node.node_id,
                runId: appliedRun,
                fileId,
                fileName: selected.name,
                revision: current.revision,
                sha256: current.sha256,
              });
            }}
          >
            提交新版本
          </button>
          {conflict && (
            <>
              <p role="alert">版本已变化，所选输出保留，可另存为新入口。</p>
              <button onClick={() => setSave(true)}>将所选输出另存到空间</button>
              <button
                onClick={() => {
                  clearRejectedVersionUpdate(node.node_id);
                  query.retry();
                }}
              >
                读回当前版本并重新确认
              </button>
            </>
          )}
        </>
      )}
      {operation && (
        <p role="status">
          原版本提交：{operation.fileName}，预期 v{operation.revision}；
          {operation.busy
            ? "处理中，关闭或切页签后继续保留。"
            : operation.state === "unknown"
              ? "结果待确认，请继续原版本提交。"
              : operation.state === "succeeded"
                ? "版本已提交。"
                : "请求已明确拒绝。"}
        </p>
      )}
      {unresolved && (
        <button disabled={busy} onClick={() => void resumeVersionUpdate(node.node_id)}>
          继续原版本提交
        </button>
      )}
      {(operation?.error || error) && <p role="alert">{operation?.error || error}</p>}
      {save && selected && (
        <SaveToWorkspaceDialog
          file={{ file_id: selected.file_id, file_name: selected.name }}
          onClose={() => setSave(false)}
          onSaved={() => {}}
        />
      )}
    </section>
  );
}
