import { useCallback, useEffect, useMemo, useState } from "react";
import { api as transport } from "../api/client";
import { HpApi } from "../api/resources";
import { HpCommandError } from "../api/types";

const api = new HpApi(transport);
type Tree = Awaited<ReturnType<HpApi["getWorkspace"]>>;
type Node = Tree["nodes"][number];

export function WorkspacePanel({
  accountId,
  currentRunId,
  conversationId,
  refreshSignal,
  onSelectDirectory,
}: {
  accountId: string | null;
  currentRunId: string | null;
  conversationId: string | null;
  refreshSignal: number;
  onSelectDirectory: (id: string | null) => void;
}) {
  const [tree, setTree] = useState<Tree | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [searchText, setSearchText] = useState("");
  const [searchType, setSearchType] = useState("");
  const [searchPurpose, setSearchPurpose] = useState("");
  const [searchTask, setSearchTask] = useState("");
  const [searchRun, setSearchRun] = useState("");
  const [searchDate, setSearchDate] = useState("");
  const [searchSummary, setSearchSummary] = useState("");
  const searchFilters = {
    name: searchText,
    content_type: searchType,
    purpose: searchPurpose,
    task_id: searchTask,
    source_run_id: searchRun,
    from_date: searchDate,
    to_date: searchDate,
    summary: searchSummary,
  };
  const [appliedFilters, setAppliedFilters] = useState<typeof searchFilters | null>(null);
  const [searchResults, setSearchResults] = useState<Awaited<
    ReturnType<HpApi["searchWorkspace"]>
  > | null>(null);
  const [space, setSpace] = useState<Awaited<ReturnType<HpApi["getWorkspaceSpace"]>> | null>(null);
  const [retention, setRetention] = useState<Awaited<ReturnType<HpApi["getFileRetention"]>> | null>(
    null,
  );
  const [parentId, setParentId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [impact, setImpact] = useState<{
    nodeId: string;
    action: "move" | "remove";
    token: string;
    affectedRuns: number;
    parentId: string;
    name: string;
  } | null>(null);
  const [grants, setGrants] = useState<
    Awaited<ReturnType<HpApi["listConversationResources"]>>["grants"]
  >([]);
  const [conversationFiles, setConversationFiles] = useState<
    Awaited<ReturnType<HpApi["listConversationResources"]>>["attachments"]
  >([]);
  const [runResources, setRunResources] = useState<Awaited<
    ReturnType<HpApi["listRunResources"]>
  > | null>(null);
  const [stopState, setStopState] = useState<string | null>(null);
  const [stoppingRuns, setStoppingRuns] = useState<string[]>([]);
  const [history, setHistory] = useState<Awaited<ReturnType<HpApi["getWorkspaceVersions"]>> | null>(
    null,
  );
  const [updateRunId, setUpdateRunId] = useState<string | null>(null);
  const [updateFileId, setUpdateFileId] = useState("");
  const [updateOperationId, setUpdateOperationId] = useState<string | null>(null);
  const [publishedFiles, setPublishedFiles] = useState<
    Awaited<ReturnType<HpApi["listRunPublishedFiles"]>>["files"]
  >([]);

  const activeUpdateRunId = updateRunId ?? currentRunId ?? "";

  useEffect(() => {
    if (!stoppingRuns.length) return;
    const timer = window.setInterval(() => {
      void Promise.all(stoppingRuns.map((runId) => api.getRun(runId)))
        .then((runs) => {
          const active = runs.filter(
            (item) =>
              item.run.status === "queued" ||
              item.run.status === "running" ||
              item.run.status === "cancelling",
          );
          setStoppingRuns(active.map((item) => item.run.run_id));
          setStopState(
            active.length
              ? `${active.length} 个 Run 正在停止，等待执行端确认`
              : "受影响 Run 已停止",
          );
        })
        .catch(() => setStopState("停止状态暂不可查询"));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [stoppingRuns]);

  const refreshAuthority = useCallback(async () => {
    if (conversationId) {
      const resources = await api.listConversationResources(conversationId);
      setGrants(resources.grants);
      setConversationFiles(resources.attachments);
    } else {
      setGrants([]);
      setConversationFiles([]);
    }
    if (currentRunId) {
      setRunResources(await api.listRunResources(currentRunId));
    } else {
      setRunResources(null);
    }
  }, [conversationId, currentRunId]);

  useEffect(() => {
    void Promise.resolve()
      .then(refreshAuthority)
      .catch(() => setError("资源授权加载失败"));
  }, [refreshAuthority, refreshSignal]);

  const refresh = useCallback(async () => {
    if (!accountId) return;
    try {
      const value = await api.getWorkspace();
      setTree(value);
      setSpace(await api.getWorkspaceSpace());
      const active = value.nodes.find((node) => node.node_id === selectedId);
      setSelectedId(active?.node_id ?? value.root_id);
      setName(active?.name ?? "");
      setParentId(active?.parent_id ?? value.root_id);
      onSelectDirectory(
        active?.kind === "directory" ? active.node_id : (active?.parent_id ?? value.root_id),
      );
      setError(null);
    } catch {
      setError("Workspace 加载失败");
    }
  }, [accountId, onSelectDirectory, selectedId]);

  useEffect(() => {
    if (!accountId) return;
    let active = true;
    void Promise.all([api.getWorkspace(), api.getWorkspaceSpace()])
      .then(([value, usage]) => {
        if (!active) return;
        setSpace(usage);
        setTree(value);
        setSelectedId(value.root_id);
        setName("");
        setParentId(value.root_id);
        onSelectDirectory(value.root_id);
        setError(null);
      })
      .catch(() => {
        if (active) setError("Workspace 加载失败");
      });
    return () => {
      active = false;
    };
  }, [accountId, refreshSignal, onSelectDirectory]);

  function selectNode(node: Node | null) {
    if (!tree) return;
    setImpact(null);
    setSelectedId(node?.node_id ?? tree.root_id);
    setName(node?.name ?? "");
    setParentId(node?.parent_id ?? tree.root_id);
    setHistory(null);
    setRetention(null);
    if (node?.file_id) {
      void api
        .getFileRetention(node.file_id)
        .then(setRetention)
        .catch(() => setError("保留原因加载失败"));
    }
    setUpdateOperationId(null);
    if (node?.kind === "file") {
      void api
        .getWorkspaceVersions(node.node_id)
        .then(setHistory)
        .catch(() => setError("版本历史加载失败"));
    }
    onSelectDirectory(
      node?.kind === "directory" ? node.node_id : (node?.parent_id ?? tree.root_id),
    );
  }

  const selected = tree?.nodes.find((node) => node.node_id === selectedId);
  const directories = tree?.nodes.filter((node) => node.kind === "directory") ?? [];
  const ordered = useMemo(() => {
    if (!tree) return [];
    const result: Array<{ node: Node; depth: number }> = [];
    function visit(parent: string, depth: number) {
      for (const node of tree!.nodes.filter((item) => item.parent_id === parent)) {
        result.push({ node, depth });
        if (node.kind === "directory") visit(node.node_id, depth + 1);
      }
    }
    visit(tree.root_id, 0);
    return result;
  }, [tree]);

  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await refresh();
      await refreshAuthority();
    } catch {
      setError("Workspace 操作失败：名称冲突、目录非空或目标不可用");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="长期 Workspace" style={{ padding: 12, maxHeight: 350, overflowY: "auto" }}>
      <strong>长期 Workspace</strong>
      {space ? (
        <p>
          账户物理文件 {space.physical_files} 个 · {space.physical_bytes} bytes； 活动入口覆盖{" "}
          {space.files_with_active_entry} 个物理文件
        </p>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setAppliedFilters(searchFilters);
          void api
            .searchWorkspace(searchFilters)
            .then(setSearchResults)
            .catch(() => setError("检索失败"));
        }}
      >
        <input
          aria-label="搜索长期文件"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
        />
        <input
          aria-label="文件类型"
          placeholder="MIME 类型"
          value={searchType}
          onChange={(event) => setSearchType(event.target.value)}
        />
        <select
          aria-label="来源类别"
          value={searchPurpose}
          onChange={(event) => setSearchPurpose(event.target.value)}
        >
          <option value="">全部来源</option>
          <option value="input">上传</option>
          <option value="output">Run 输出</option>
        </select>
        <input
          aria-label="来源 Task ID"
          placeholder="Task ID"
          value={searchTask}
          onChange={(event) => setSearchTask(event.target.value)}
        />
        <input
          aria-label="来源 Run ID"
          placeholder="Run ID"
          value={searchRun}
          onChange={(event) => setSearchRun(event.target.value)}
        />
        <input
          aria-label="创建日期"
          type="date"
          value={searchDate}
          onChange={(event) => setSearchDate(event.target.value)}
        />
        <input
          aria-label="摘要关键词"
          placeholder="摘要关键词"
          value={searchSummary}
          onChange={(event) => setSearchSummary(event.target.value)}
        />
        <button type="submit">搜索</button>
      </form>
      {searchResults ? (
        <section aria-label="长期文件搜索结果">
          {searchResults.items.map((item) => (
            <button
              key={item.node_id}
              type="button"
              onClick={() =>
                selectNode(tree?.nodes.find((node) => node.node_id === item.node_id) ?? null)
              }
            >
              {item.name} · {item.content_type ?? "未知类型"}
            </button>
          ))}
          {searchResults.next_after ? (
            <button
              type="button"
              onClick={() =>
                void api
                  .searchWorkspace(appliedFilters ?? searchFilters, searchResults.next_after)
                  .then((page) =>
                    setSearchResults({
                      items: [...searchResults.items, ...page.items],
                      next_after: page.next_after,
                    }),
                  )
              }
            >
              更多
            </button>
          ) : null}
        </section>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      <div>
        <button type="button" onClick={() => selectNode(null)}>
          根目录
        </button>
        {ordered.map(({ node, depth }) => (
          <div key={node.node_id} style={{ paddingLeft: 12 * (depth + 1) }}>
            <button
              type="button"
              aria-pressed={selectedId === node.node_id}
              onClick={() => selectNode(node)}
            >
              {node.kind === "directory" ? "📁 " : "📄 "}
              {node.name}
            </button>
            {node.kind === "file" && node.file_id ? (
              <a href={`/api/v1/files/${node.file_id}/content`}>下载</a>
            ) : null}
          </div>
        ))}
      </div>
      {retention ? (
        <section aria-label="保留与空间">
          <p>物理对象 {retention.physical_bytes} bytes（相同文件的多个入口只计一次）</p>
          <p>移除长期入口不等于释放物理空间；引用释放后由统一 GC 回收。</p>
          <ul>
            {Object.entries(retention.references)
              .filter(([, count]) => count > 0)
              .map(([reason, count]) => (
                <li key={reason}>
                  {reason}: {count}
                </li>
              ))}
          </ul>
        </section>
      ) : null}
      {selected?.source ? (
        <p>
          来源：{selected.source.purpose === "output" ? "Run 输出" : "上传"}
          {selected.source.run_id ? ` · Run ${selected.source.run_id.slice(0, 8)}` : null}
          {selected.source.conversation_id
            ? ` · Conversation ${selected.source.conversation_id.slice(0, 8)}`
            : null}
          {` · ${selected.source.size_bytes} bytes`}
        </p>
      ) : null}
      {selected?.kind === "file" ? (
        <section aria-label="Workspace 版本">
          <p>
            当前版本：{history?.current.revision ?? "不可变入口"}
            {history?.current.destination_id
              ? ` · Destination ${history.current.destination_id.slice(0, 8)}`
              : null}
          </p>
          {!selected.destination_id ? (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void mutate(async () => {
                  await api.upgradeWorkspaceFile(selected.node_id);
                  setHistory(await api.getWorkspaceVersions(selected.node_id));
                })
              }
            >
              启用版本历史
            </button>
          ) : null}
          {history?.revisions.map((revision) => (
            <div key={revision.revision}>
              v{revision.revision} · {revision.source.purpose === "output" ? "Run 输出" : "上传"}
              {revision.source.run_id ? ` · Run ${revision.source.run_id.slice(0, 8)}` : null}
              {revision.source.conversation_id
                ? ` · Conversation ${revision.source.conversation_id.slice(0, 8)}`
                : null}
              {" · "}
              <a href={`/api/v1/files/${revision.file_id}/content`}>下载</a>
            </div>
          ))}
          {history?.current.destination_id ? (
            <div>
              <label>
                编辑 Run ID
                <input
                  aria-label="编辑 Run ID"
                  value={activeUpdateRunId}
                  onChange={(event) => {
                    setUpdateRunId(event.target.value);
                    setPublishedFiles([]);
                    setUpdateOperationId(null);
                  }}
                />
              </label>
              <button
                type="button"
                disabled={!activeUpdateRunId || busy}
                onClick={() =>
                  void api
                    .listRunPublishedFiles(activeUpdateRunId)
                    .then((page) => {
                      setPublishedFiles(page.files);
                      if (page.files.length === 1 && page.files[0])
                        setUpdateFileId(page.files[0].file_id);
                    })
                    .catch(() => setError("Run 输出加载失败"))
                }
              >
                选择 Run 已发布输出
              </button>
              {publishedFiles.length ? (
                <select
                  aria-label="Run 已发布输出"
                  value={updateFileId}
                  onChange={(event) => {
                    setUpdateFileId(event.target.value);
                    setUpdateOperationId(null);
                  }}
                >
                  <option value="">选择输出</option>
                  {publishedFiles.map((file) => (
                    <option key={file.file_id} value={file.file_id}>
                      {file.name}
                    </option>
                  ))}
                </select>
              ) : null}
              <label>
                已发布输出文件 ID
                <input
                  aria-label="已发布输出文件 ID"
                  value={updateFileId}
                  onChange={(event) => {
                    setUpdateFileId(event.target.value);
                    setUpdateOperationId(null);
                  }}
                />
              </label>
              <button
                type="button"
                disabled={busy || !activeUpdateRunId || !updateFileId}
                onClick={() => {
                  const operationId = updateOperationId ?? crypto.randomUUID();
                  setUpdateOperationId(operationId);
                  setBusy(true);
                  void api
                    .updateWorkspaceFile(
                      selected.node_id,
                      activeUpdateRunId,
                      updateFileId,
                      history.current.revision!,
                      history.current.sha256,
                      operationId,
                    )
                    .then(async () => {
                      setHistory(await api.getWorkspaceVersions(selected.node_id));
                      setTree(await api.getWorkspace());
                      setUpdateOperationId(null);
                      setError(null);
                    })
                    .catch((cause: unknown) => {
                      if (
                        cause instanceof HpCommandError &&
                        cause.code === "workspace_version_conflict"
                      ) {
                        setError(`版本冲突：输出 ${updateFileId} 已保留，可另存到 Workspace。`);
                      } else {
                        setError("版本提交失败；可使用相同操作再次尝试。");
                      }
                    })
                    .finally(() => setBusy(false));
                }}
              >
                提交新版本
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
      <label>
        名称
        <input
          aria-label="Workspace 名称"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setImpact(null);
          }}
        />
      </label>
      <label>
        目标目录
        <select
          aria-label="Workspace 目标目录"
          value={parentId}
          onChange={(event) => {
            setParentId(event.target.value);
            setImpact(null);
          }}
        >
          {directories.map((directory) => (
            <option key={directory.node_id} value={directory.node_id}>
              {directory.name || "根目录"}
            </option>
          ))}
        </select>
      </label>
      <div>
        <button
          type="button"
          disabled={busy || !name || !parentId}
          onClick={() => void mutate(() => api.createWorkspaceDirectory(parentId, name))}
        >
          新建目录
        </button>
        <button
          type="button"
          disabled={busy || !selected || selected.parent_id === null}
          onClick={() =>
            selected &&
            void api
              .previewWorkspaceNode(selected.node_id)
              .then((preview) =>
                setImpact({
                  nodeId: selected.node_id,
                  action: "move",
                  token: preview.preview_token,
                  affectedRuns: preview.potentially_affected_runs.length,
                  parentId,
                  name,
                }),
              )
              .catch(() => setError("影响预览加载失败"))
          }
        >
          预览改名或移动影响
        </button>
        <button
          type="button"
          disabled={busy || !selected || selected.parent_id === null}
          onClick={() =>
            selected &&
            void api
              .previewWorkspaceNode(selected.node_id)
              .then((preview) =>
                setImpact({
                  nodeId: selected.node_id,
                  action: "remove",
                  token: preview.preview_token,
                  affectedRuns: preview.potentially_affected_runs.length,
                  parentId,
                  name,
                }),
              )
              .catch(() => setError("影响预览加载失败"))
          }
        >
          预览移除影响
        </button>
        {impact && selected?.node_id === impact.nodeId ? (
          <div>
            <p>可能影响 {impact.affectedRuns} 个活动 Run；提交时重新校验目录和授权版本。</p>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void mutate(async () => {
                  if (impact.action === "move") {
                    await api.moveWorkspaceNode(
                      impact.nodeId,
                      impact.parentId,
                      impact.name,
                      impact.token,
                    );
                  } else {
                    await api.removeWorkspaceNode(impact.nodeId, impact.token);
                  }
                  setImpact(null);
                })
              }
            >
              确认{impact.action === "move" ? "改名或移动" : "移除"}
            </button>
          </div>
        ) : null}
      </div>
      <aside>
        <p>所有者可浏览：当前账户 Workspace 全部入口。Agent 已授权：{grants.length} 条规则。</p>
        {conversationId && selected && selected.parent_id !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void mutate(() =>
                api.grantConversationResource(
                  conversationId,
                  selected.node_id,
                  ["list_metadata", "read_content"],
                  selected.kind === "directory",
                ),
              )
            }
          >
            授权当前对话读取{selected.kind === "directory" ? "目录及后代" : "文件"}
          </button>
        ) : null}
        {conversationId && selected?.kind === "file" ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void mutate(() =>
                api.grantConversationResource(
                  conversationId,
                  selected.node_id,
                  ["list_metadata", "read_content", "update_content"],
                  false,
                ),
              )
            }
          >
            授权当前对话修改文件
          </button>
        ) : null}
        {grants.map((grant) => (
          <div key={grant.grant_id}>
            {grant.name} · {grant.operation}
            {grant.recursive ? "（递归）" : ""}
            <button
              type="button"
              disabled={busy || !conversationId}
              onClick={() =>
                void mutate(async () => {
                  const result = await api.revokeConversationResource(
                    conversationId!,
                    grant.grant_id,
                  );
                  const active = result.affected_runs.filter(
                    (run) => run.stop_state === "stopping",
                  );
                  setStoppingRuns(active.map((run) => run.run_id));
                  setStopState(
                    active.length
                      ? `${active.length} 个 Run 正在停止，等待执行端确认`
                      : result.affected_runs.length
                        ? "受影响 Run 已停止"
                        : "授权已撤销，无需停止活动 Run",
                  );
                })
              }
            >
              撤销
            </button>
          </div>
        ))}
        <p>对话已选择附件：{conversationFiles.filter((file) => file.available).length}</p>
        {conversationFiles
          .filter((file) => file.available)
          .map((file) => (
            <div key={file.file_id}>
              {file.name}
              <button
                type="button"
                disabled={busy || !conversationId}
                onClick={() =>
                  void mutate(async () => {
                    const result = await api.revokeConversationAttachment(
                      conversationId!,
                      file.file_id,
                    );
                    setStoppingRuns(result.affected_runs.map((run) => run.run_id));
                    setStopState(
                      result.affected_runs.length
                        ? `${result.affected_runs.length} 个 Run 正在停止，等待执行端确认`
                        : "附件已从对话可用资料撤销",
                    );
                  })
                }
              >
                撤销附件可用性
              </button>
            </div>
          ))}
        {stopState ? <p>{stopState}</p> : null}
        <p>本 Run 候选：{runResources?.count ?? 0}；已固定/读取分别标注。</p>
        {runResources?.candidates.map((candidate) => (
          <div key={candidate.node_id}>
            {candidate.name} · 候选
            {candidate.fixed ? " · 已固定" : ""}
            {candidate.read ? " · 已读取" : ""}
          </div>
        ))}
        {runResources?.next && currentRunId ? (
          <button
            type="button"
            onClick={() =>
              void api.listRunResources(currentRunId, runResources.next).then((page) =>
                setRunResources({
                  ...page,
                  candidates: [...runResources.candidates, ...page.candidates],
                }),
              )
            }
          >
            加载更多候选
          </button>
        ) : null}
        <p>本次运行：{currentRunId ? currentRunId.slice(0, 8) : "无"}（临时资源）</p>
      </aside>
    </section>
  );
}
