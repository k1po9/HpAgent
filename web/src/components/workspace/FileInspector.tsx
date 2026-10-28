import { FormActions } from "../ui";
import { Button } from "../ui";
import { InspectorTabs } from "../shell/InspectorTabs";
import { useEffect, useState, type ReactNode } from "react";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell, type Inspector } from "../../store/shell";
import type { HpWorkspaceNode } from "../../api/types";
import { FilePreview } from "./FilePreview";
import { FileVersions } from "./FileVersions";
import { WorkspaceMutationDialog } from "./WorkspaceMutationDialog";
import { UseInConversationDialog } from "./UseInConversationDialog";
import { SubjectPicker } from "./SubjectPicker";
import { GrantEditor } from "./GrantEditor";
import type { Subject } from "./workspaceOperations";
import { useWorkspaceQuery } from "./useWorkspaceQuery";
import { formatBytes, nodePath } from "./workspacePresentation";
const tabs = [
  { id: "preview", label: "预览" },
  { id: "details", label: "详情" },
  { id: "versions", label: "版本历史" },
  { id: "usage", label: "AI 使用范围" },
] as const;
export function FileInspector({ inspector }: { inspector: Inspector }) {
  const tree = useWorkspace((s) => s.tree);
  const error = useWorkspace((s) => s.error);
  const loading = useWorkspace((s) => s.loading);
  useEffect(() => {
    void useWorkspace
      .getState()
      .loadTree()
      .catch(() => {});
  }, []);
  const node = tree?.nodes.find((n) => n.node_id === inspector.objectId);
  if (!tree || error)
    return (
      <p role={error ? "alert" : "status"}>
        {error || "正在加载对象…"}
        {error && (
          <Button
            type="button"
            onClick={() =>
              void useWorkspace
                .getState()
                .loadTree(true)
                .catch(() => {})
            }
          >
            重试对象
          </Button>
        )}
      </p>
    );
  if (!node)
    return (
      <div role="alert">
        <p>对象不可用。</p>
        <Button
          type="button"
          disabled={loading}
          onClick={() =>
            void useWorkspace
              .getState()
              .loadTree(true)
              .catch(() => {})
          }
        >
          重新验证对象
        </Button>
        <Button type="button" onClick={() => useShell.getState().navigate({ screen: "workspace" })}>
          返回空间
        </Button>
      </div>
    );
  return <FileBody key={node.node_id} node={node} inspector={inspector} />;
}
/** A visited panel keeps its DOM and query owner; unvisited panels remain lazy. */
function FilePanel({ id, active, children }: { id: string; active: boolean; children: ReactNode }) {
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  return (
    <section
      role="tabpanel"
      id={`file-panel-${id}`}
      aria-labelledby={`file-tab-${id}`}
      hidden={!active}
      inert={!active}
    >
      {(active || visited) && children}
    </section>
  );
}
function FileBody({ node, inspector }: { node: HpWorkspaceNode; inspector: Inspector }) {
  const tree = useWorkspace((s) => s.tree);
  const [mutation, setMutation] = useState(false);
  const [using, setUsing] = useState(false);
  const [subject, setSubject] = useState<Subject>();
  const tab = tabs.some(
    (t) => t.id === inspector.tab && (node.kind === "file" || ["details", "usage"].includes(t.id)),
  )
    ? inspector.tab
    : node.kind === "directory"
      ? "details"
      : "preview";
  useEffect(() => {
    if (inspector.tab && inspector.tab !== tab) useShell.getState().setInspectorTab(tab);
  }, [inspector.tab, tab]);
  const metadata = useWorkspaceQuery(
    node.kind === "file" && node.file_id ? `file:${node.file_id}` : null,
    () => workspaceApi.getFile(node.file_id!),
  );
  if (metadata.error)
    return (
      <p role="alert">
        文件信息不可用。
        <Button type="button" onClick={metadata.retry}>
          重试文件信息
        </Button>
      </p>
    );
  return (
    <div className="hp-file-inspector">
      <h3>
        {node.kind === "directory" ? "目录" : "文件"}：{node.name || "根目录"}
      </h3>
      <p className="hp-object-path">{nodePath(tree, node.node_id)}</p>
      <FormActions className="hp-file-actions">
        {metadata.data?.file.status === "ready" && (
          <a href={`/api/v1/files/${encodeURIComponent(node.file_id!)}/content`} download>
            下载
          </a>
        )}
        <Button
          type="button"
          disabled={node.kind === "file" && !metadata.data}
          onClick={() => setUsing(true)}
        >
          在对话中使用
        </Button>
        {node.parent_id !== null && (
          <Button
            type="button"
            disabled={node.kind === "file" && !metadata.data}
            onClick={() => setMutation(true)}
          >
            改名、移动或移除入口
          </Button>
        )}
      </FormActions>
      {node.kind === "file" && <FileArtifactLink node={node} />}
      <InspectorTabs
        prefix="file"
        label="文件信息"
        tabs={tabs.filter((t) => node.kind === "file" || ["details", "usage"].includes(t.id))}
        selected={tab}
      />
      {node.kind === "file" && (
        <FilePanel id="preview" active={tab === "preview"}>
          {metadata.data ? (
            <FilePreview key={node.file_id} file={metadata.data.file} />
          ) : (
            !metadata.error && <p role="status">正在读取文件信息…</p>
          )}
        </FilePanel>
      )}
      <FilePanel id="details" active={tab === "details"}>
        <>
          <dl>
            <dt>类型</dt>
            <dd>
              {node.kind === "directory" ? "目录" : (metadata.data?.file.content_type ?? "—")}
            </dd>
            <dt>大小</dt>
            <dd>
              {node.kind === "directory"
                ? "—"
                : formatBytes(metadata.data?.file.size_bytes ?? node.source?.size_bytes)}
            </dd>
            <dt>来源</dt>
            <dd>
              {node.source?.purpose === "output"
                ? "AI 输出"
                : node.source?.purpose === "input"
                  ? "上传"
                  : "—"}
            </dd>
          </dl>
          {node.kind === "file" && node.file_id && <FileDetails node={node} />}
        </>
      </FilePanel>
      {node.kind === "file" && (
        <FilePanel id="versions" active={tab === "versions"}>
          <FileVersions node={node} />
        </FilePanel>
      )}
      <FilePanel id="usage" active={tab === "usage"}>
        <>
          <p>仅管理所选主体；历史使用记录不代表当前有效授权或完整主体列表。</p>
          <SubjectPicker value={subject} onChange={setSubject} />
          {subject ? (
            <GrantEditor node={node} subject={subject} />
          ) : (
            <p>选择对话或任务查看其权限。</p>
          )}
          {node.kind === "file" && <FileUsage node={node} />}
        </>
      </FilePanel>
      {mutation && <WorkspaceMutationDialog node={node} onClose={() => setMutation(false)} />}
      {using && <UseInConversationDialog node={node} onClose={() => setUsing(false)} />}
    </div>
  );
}
function FileArtifactLink({ node }: { node: HpWorkspaceNode }) {
  const trace = useWorkspaceQuery(`trace:${node.node_id}`, () =>
    workspaceApi.getWorkspaceTrace(node.node_id),
  );
  const artifact = trace.data?.artifact;
  return (
    <div>
      {artifact && (
        <>
          <p>空间保留文件字节；交互预览与自然语言修改在 HTML 成果中打开。</p>
          <Button
            type="button"
            id={`file-artifact-${node.node_id}`}
            onClick={() =>
              useShell.getState().openInspector(
                {
                  kind: "artifact",
                  objectId: artifact.artifact_id,
                  versionId: artifact.artifact_version_id,
                  origin: {
                    triggerId: `file-artifact-${node.node_id}`,
                    workId: trace.data?.source_work_id ?? undefined,
                    runId: trace.data?.source_run_id ?? undefined,
                  },
                },
                true,
              )
            }
          >
            打开交互成果 v{artifact.version}
          </Button>
        </>
      )}
      {trace.error && (
        <p role="alert">
          交互成果来源暂不可用。
          <Button type="button" onClick={trace.retry}>
            重试成果来源
          </Button>
        </p>
      )}
    </div>
  );
}
function FileDetails({ node }: { node: HpWorkspaceNode }) {
  const retention = useWorkspaceQuery(`retention:${node.file_id}`, () =>
    workspaceApi.getFileRetention(node.file_id!),
  );
  const trace = useWorkspaceQuery(`trace:${node.node_id}`, () =>
    workspaceApi.getWorkspaceTrace(node.node_id),
  );
  const [lineageOpen, setLineageOpen] = useState(false);
  const lineage = useWorkspaceQuery(lineageOpen ? `lineage:${node.file_id}` : null, () =>
    workspaceApi.getFileLineage(node.file_id!),
  );
  return (
    <>
      {retention.data && (
        <section aria-label="保留与空间">
          <p>
            物理对象 {retention.data.physical_bytes} bytes；移除入口不等于释放空间，引用释放后由 GC
            回收。
          </p>
          <ul>
            {Object.entries(retention.data.references)
              .filter(([, n]) => n > 0)
              .map(([reason, n]) => (
                <li key={reason}>
                  {reason}：{n}
                </li>
              ))}
          </ul>
        </section>
      )}
      {retention.error && (
        <p role="alert">
          保留信息加载失败。
          <Button type="button" onClick={retention.retry}>
            重试保留信息
          </Button>
        </p>
      )}
      {trace.data?.source_run_id && (
        <Button
          type="button"
          onClick={() =>
            useShell
              .getState()
              .openInspector({ kind: "run", objectId: trace.data!.source_run_id! }, true)
          }
        >
          查看来源执行
        </Button>
      )}
      {trace.data?.source_work_id && (
        <Button
          type="button"
          id={`file-source-task-${node.node_id}`}
          onClick={() =>
            useShell.getState().openInspector(
              {
                kind: "task",
                objectId: trace.data!.source_work_id!,
                origin: {
                  triggerId: `file-source-task-${node.node_id}`,
                  workId: trace.data!.source_work_id!,
                  runId: trace.data!.source_run_id ?? undefined,
                },
              },
              true,
            )
          }
        >
          查看来源任务
        </Button>
      )}
      {trace.error && (
        <p role="alert">
          来源查询失败。
          <Button type="button" onClick={trace.retry}>
            重试来源
          </Button>
        </p>
      )}
      <Button type="button" onClick={() => setLineageOpen((v) => !v)}>
        {lineageOpen ? "收起来源链" : "查看来源链"}
      </Button>
      {lineageOpen && (
        <>
          {lineage.loading && <p>正在加载来源链…</p>}
          {lineage.error && (
            <p role="alert">
              来源链不可用。
              <Button type="button" onClick={lineage.retry}>
                重试来源链
              </Button>
            </p>
          )}
          {lineage.data?.files.map((f) => (
            <p key={f.file_id}>
              {f.file_name} · {f.purpose === "output" ? "输出" : "输入"}
            </p>
          ))}
        </>
      )}
    </>
  );
}
function FileUsage({ node }: { node: HpWorkspaceNode }) {
  const trace = useWorkspaceQuery(`trace:${node.node_id}`, () =>
    workspaceApi.getWorkspaceTrace(node.node_id),
  );
  return (
    <section aria-label="历史使用记录">
      <h4>历史使用记录</h4>
      {trace.loading && <p>正在读取历史…</p>}
      {trace.error && (
        <p role="alert">
          使用记录暂不可用。
          <Button type="button" onClick={trace.retry}>
            重试历史
          </Button>
        </p>
      )}
      {trace.data?.run_usage_truncated && <p>仅展示部分历史，接口未提供更多页。</p>}
      {trace.data?.run_usage.length === 0 && <p>暂无可展示的历史记录；不代表没有当前授权。</p>}
      {trace.data?.run_usage.map((r) => (
        <p key={r.run_id}>
          <Button
            type="button"
            onClick={() =>
              useShell.getState().openInspector({ kind: "run", objectId: r.run_id }, true)
            }
          >
            查看执行 {r.run_id}
          </Button>{" "}
          · {r.fixed_at ? "已固定" : "候选"} · {r.materialized_at ? "已物化" : "未确认物化"} ·{" "}
          {r.first_read_at ? `首次读取 ${r.first_read_at}` : "未确认读取"}
        </p>
      ))}
    </section>
  );
}
