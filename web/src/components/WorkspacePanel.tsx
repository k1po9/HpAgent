import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { newIdempotencyKey } from "../utils/idempotency";
import { api as transport } from "../api/client";
import { HpApi } from "../api/resources";
import { HpCommandError, type HpRunStatus } from "../api/types";

const api = new HpApi(transport);
type Tree = Awaited<ReturnType<HpApi["getWorkspace"]>>;
type Node = Tree["nodes"][number];

export function WorkspacePanel({
  accountId,
  currentRunId,
  candidateRunId,
  currentRunStatus,
  conversationId,
  refreshSignal,
  onSelectDirectory,
  view = "all",
  directoryId,
}: {
  accountId: string | null;
  currentRunId: string | null;
  candidateRunId: string | null;
  currentRunStatus: HpRunStatus | null;
  conversationId: string | null;
  refreshSignal: number;
  onSelectDirectory: (id: string | null) => void;
  view?: "all" | "files" | "authority";
  directoryId?: string;
}) {
  const [tree, setTree] = useState<Tree | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [newDirectoryName, setNewDirectoryName] = useState("");
  const selectedNodeRef = useRef<string | null>(null);
  const [useForConversation, setUseForConversation] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingAuthorization, setPendingAuthorization] = useState<{
    nodeId: string;
    conversationId: string;
    name: string;
  } | null>(null);
  const operationScope = useRef({ accountId, conversationId });
  useLayoutEffect(() => {
    operationScope.current = { accountId, conversationId };
    return () => {
      operationScope.current = { accountId: null, conversationId: null };
    };
  }, [accountId, conversationId]);
  const [searchText, setSearchText] = useState("");
  const [searchType, setSearchType] = useState("");
  const [searchPurpose, setSearchPurpose] = useState("");
  const [searchWork, setSearchWork] = useState("");
  const [searchRun, setSearchRun] = useState("");
  const [searchDate, setSearchDate] = useState("");
  const [searchSummary, setSearchSummary] = useState("");
  const searchFilters = {
    name: searchText,
    content_type: searchType,
    purpose: searchPurpose,
    work_id: searchWork,
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
  const [authorityError, setAuthorityError] = useState<string | null>(null);
  // Every committed selection gets a new identity, including A → B → A.
  const authorityScope = useRef({ accountId, conversationId, candidateRunId, currentRunStatus });
  const authorityRequest = useRef(0);
  useLayoutEffect(() => {
    authorityScope.current = { accountId, conversationId, candidateRunId, currentRunStatus };
    return () => {
      authorityScope.current = {
        accountId: null,
        conversationId: null,
        candidateRunId: null,
        currentRunStatus: null,
      };
    };
  }, [accountId, conversationId, candidateRunId, currentRunStatus]);
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
    const scope = authorityScope.current;
    if (
      scope.accountId !== accountId ||
      scope.conversationId !== conversationId ||
      scope.candidateRunId !== candidateRunId ||
      scope.currentRunStatus !== currentRunStatus
    )
      return;
    const request = ++authorityRequest.current;
    const isCurrent = () =>
      authorityScope.current === scope && authorityRequest.current === request;
    try {
      if (conversationId) {
        const resources = await api.listConversationResources(conversationId);
        if (!isCurrent()) return;
        setGrants(resources.grants);
        setConversationFiles(resources.attachments);
      } else {
        setGrants([]);
        setConversationFiles([]);
      }
      if (candidateRunId) {
        const resources = await api.listRunResources(candidateRunId);
        if (!isCurrent()) return;
        setRunResources(resources);
      } else {
        setRunResources(null);
      }
      if (isCurrent()) setAuthorityError(null);
    } catch {
      if (isCurrent()) setAuthorityError("资源授权加载失败");
    }
  }, [accountId, conversationId, candidateRunId, currentRunStatus]);

  useEffect(() => {
    const scope = authorityScope.current;
    void Promise.resolve().then(() => {
      if (authorityScope.current !== scope) return;
      setGrants([]);
      setConversationFiles([]);
      setRunResources(null);
      setAuthorityError(null);
      return refreshAuthority();
    });
  }, [refreshAuthority, refreshSignal]);

  const applyTree = useCallback(
    (value: Tree) => {
      const active = value.nodes.find(
        (node) => node.node_id === (directoryId ?? selectedNodeRef.current),
      );
      const identity = active?.node_id ?? value.root_id;
      selectedNodeRef.current = identity;
      setTree(value);
      setSelectedId(identity);
      setName(active?.name ?? "");
      setParentId(active?.parent_id ?? value.root_id);
      onSelectDirectory(
        active?.kind === "directory" ? active.node_id : (active?.parent_id ?? value.root_id),
      );
    },
    [onSelectDirectory, directoryId],
  );

  const refresh = useCallback(async () => {
    if (!accountId) return;
    try {
      const value = await api.getWorkspace();
      applyTree(value);
      setSpace(await api.getWorkspaceSpace());
      setError(null);
    } catch {
      setError("Workspace 加载失败");
    }
  }, [accountId, applyTree]);

  useEffect(() => {
    if (!accountId) return;
    let active = true;
    void Promise.all([api.getWorkspace(), api.getWorkspaceSpace()])
      .then(([value, usage]) => {
        if (!active) return;
        setSpace(usage);
        applyTree(value);
        setError(null);
      })
      .catch(() => {
        if (active) setError("Workspace 加载失败");
      });
    return () => {
      active = false;
    };
  }, [accountId, refreshSignal, applyTree]);

  function selectNode(node: Node | null) {
    if (!tree) return;
    setImpact(null);
    selectedNodeRef.current = node?.node_id ?? tree.root_id;
    setSelectedId(selectedNodeRef.current);
    setNewDirectoryName("");
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
    } catch (cause) {
      setError(
        cause instanceof HpCommandError
          ? `${cause.message}（请求 ${cause.error.request_id}）`
          : "Workspace 操作失败，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  const createParentId =
    selected?.kind === "directory"
      ? selected.node_id
      : (selected?.parent_id ?? tree?.root_id ?? "");
  function pathFor(nodeId: string): string {
    const parts: string[] = [];
    let node = tree?.nodes.find((item) => item.node_id === nodeId);
    while (node) {
      if (node.name) parts.unshift(node.name);
      node = tree?.nodes.find((item) => item.node_id === node?.parent_id);
    }
    return "/" + parts.join("/");
  }

  async function authorizeUpload(nodeId: string, targetConversation: string) {
    const resources = await api.listConversationResources(targetConversation);
    const operations = (["list_metadata", "read_content"] as const).filter(
      (operation) =>
        !resources.grants.some(
          (grant) => grant.node_id === nodeId && grant.operation === operation,
        ),
    );
    if (operations.length)
      await api.grantConversationResource(targetConversation, nodeId, operations, false);
  }

  return (
    <section className="hp-workspace-panel" aria-label="长期 Workspace" style={{ padding: 12 }}>
      <strong>{view === "authority" ? "资料授权" : "长期 Workspace"}</strong>
      {notice ? <p role="status">{notice}</p> : null}
      {pendingAuthorization && pendingAuthorization.conversationId === conversationId ? (
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void mutate(async () => {
              await authorizeUpload(
                pendingAuthorization.nodeId,
                pendingAuthorization.conversationId,
              );
              setPendingAuthorization(null);
              setNotice("文件已授权，下一次执行可读取。");
            })
          }
        >
          补授权：{pendingAuthorization.name}
        </button>
      ) : null}
      <div hidden={view === "authority"}>
        <label>
          上传用途
          <select
            aria-label="上传用途"
            disabled={busy || !conversationId}
            value={useForConversation && conversationId ? "conversation" : "storage"}
            onChange={(event) => setUseForConversation(event.target.value === "conversation")}
          >
            <option value="conversation">上传并供当前对话使用</option>
            <option value="storage">仅保存到长期 Workspace</option>
          </select>
        </label>
        {!conversationId ? <p>当前没有选中的对话，上传仅保存到长期目录。</p> : null}
        <label>
          上传到 Workspace
          <input
            aria-label="上传到 Workspace"
            type="file"
            disabled={!tree || busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file || !tree) return;
              const destination =
                selected?.kind === "directory"
                  ? selected.node_id
                  : (selected?.parent_id ?? tree.root_id);
              const scope = operationScope.current;
              const targetConversation = useForConversation ? conversationId : null;
              void mutate(async () => {
                setNotice(null);
                const created = await api.createWorkspaceUpload(file, newIdempotencyKey());
                const ready = await api.uploadContent(created.content_url, file);
                const saved = await api.saveWorkspaceFile(
                  destination,
                  ready.file_id,
                  file.name,
                  newIdempotencyKey(),
                );
                if (operationScope.current !== scope) return;
                if (targetConversation) {
                  try {
                    await authorizeUpload(saved.node_id, targetConversation);
                    if (operationScope.current !== scope) return;
                    setPendingAuthorization(null);
                    setNotice("文件已保存并授权当前对话，下一次执行可读取。");
                  } catch {
                    if (operationScope.current !== scope) return;
                    setPendingAuthorization({
                      nodeId: saved.node_id,
                      conversationId: targetConversation,
                      name: file.name,
                    });
                    setNotice("文件已保存，授权未完成；请补授权，无需重新上传。");
                  }
                } else {
                  setNotice("文件已保存到长期 Workspace。可在资料授权页允许对话使用。");
                }
              });
              event.target.value = "";
            }}
          />
        </label>
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
            placeholder="文件类型，例如 text/plain"
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
            aria-label="来源 Work ID"
            placeholder="来源工作编号（可不填）"
            value={searchWork}
            onChange={(event) => setSearchWork(event.target.value)}
          />
          <input
            aria-label="来源 Run ID"
            placeholder="来源执行编号（可不填）"
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
      </div>
      {error ? <p role="alert">{error}</p> : null}
      <p>所选位置：{pathFor(selectedId ?? tree?.root_id ?? "")}</p>
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
      <div hidden={view === "authority"}>
        <form
          aria-label="新建子目录"
          onSubmit={(event) => {
            event.preventDefault();
            void mutate(async () => {
              const created = await api.createWorkspaceDirectory(createParentId, newDirectoryName);
              selectedNodeRef.current = created.node_id;
              setNewDirectoryName("");
              setNotice("目录已创建。");
            });
          }}
        >
          <p>在 {pathFor(createParentId)} 中新建</p>
          <label>
            新目录名称
            <input
              aria-label="新目录名称"
              value={newDirectoryName}
              onChange={(event) => setNewDirectoryName(event.target.value)}
            />
          </label>
          <button type="submit" disabled={busy || !newDirectoryName.trim() || !createParentId}>
            新建目录
          </button>
        </form>
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
                  产生新版本的执行编号
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
                    const operationId = updateOperationId ?? newIdempotencyKey();
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
      </div>
      <aside hidden={view === "files"}>
        <p>新增授权在下一次执行生效；正在运行的任务保留创建时的候选范围。</p>
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
        {authorityError ? <p role="alert">{authorityError}</p> : null}
        <p>
          {candidateRunId
            ? `本次执行可选资料：${runResources?.count ?? 0}；已固定/读取分别标注。`
            : "当前没有可选择资料的活动执行。"}
        </p>
        {runResources?.candidates.map((candidate) => (
          <div key={candidate.node_id}>
            {candidate.name} · 候选
            {candidate.fixed ? " · 已固定" : ""}
            {candidate.read ? " · 已读取" : ""}
          </div>
        ))}
        {runResources?.next && candidateRunId ? (
          <button
            type="button"
            onClick={() => {
              const scope = authorityScope.current;
              const request = authorityRequest.current;
              void api
                .listRunResources(candidateRunId, runResources.next)
                .then((page) => {
                  if (authorityScope.current !== scope || authorityRequest.current !== request)
                    return;
                  setRunResources({
                    ...page,
                    candidates: [...runResources.candidates, ...page.candidates],
                  });
                })
                .catch(() => {
                  if (authorityScope.current === scope && authorityRequest.current === request)
                    setAuthorityError("资源候选加载失败");
                });
            }}
          >
            加载更多候选
          </button>
        ) : null}
        <p>本次运行：{currentRunId ? currentRunId.slice(0, 8) : "无"}（临时资源）</p>
      </aside>
    </section>
  );
}
