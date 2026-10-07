import { useEffect, useMemo, useState } from "react";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import type { HpWorkspaceFilters } from "../../api/types";
import { ancestors, workspaceIndex, compareNodes } from "./workspacePresentation";
import { FileList } from "./FileList";
import { UploadToWorkspaceDialog } from "./UploadToWorkspaceDialog";
import { useWorkspaceQuery } from "./useWorkspaceQuery";
import { commandError } from "../../utils/commands";
export function WorkspaceScreen() {
  const state = useWorkspace();
  const route = useShell((s) => s.route);
  const canUpload = useAuth((s) => s.capabilities?.file_upload === true);
  const [upload, setUpload] = useState(false);
  const [filters, setFilters] = useState<HpWorkspaceFilters>(state.filters);
  const searchMode = state.searchMode;
  const setSearchMode = (value: boolean) => useWorkspace.setState({ searchMode: value });
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const tree = state.tree;
  const index = useMemo(() => (tree ? workspaceIndex(tree) : null), [tree]);
  const directoryId = route.directoryId ?? tree?.root_id ?? "";
  const space = useWorkspaceQuery("space", () => workspaceApi.getWorkspaceSpace());
  useEffect(() => {
    void useWorkspace
      .getState()
      .loadTree()
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (
      tree &&
      (!index?.nodes.has(directoryId) || index.nodes.get(directoryId)?.kind !== "directory")
    ) {
      useShell.getState().requestResourceChange(() => {
        const latest = useWorkspace.getState().tree;
        const shell = useShell.getState();
        if (
          !latest ||
          shell.route.screen !== "workspace" ||
          shell.route.directoryId !== route.directoryId
        )
          return;
        if (workspaceIndex(latest).nodes.get(directoryId)?.kind === "directory") return;
        shell.navigate(
          { screen: "workspace", directoryId: latest.root_id, inspector: shell.route.inspector },
          true,
        );
        if (!useShell.getState().pendingRoute)
          useWorkspace.setState({
            allFiles: false,
            searchMode: false,
            notice: "目录不存在或不可访问，已返回根目录。",
          });
      });
    }
  }, [tree, index, directoryId, route.directoryId, route.inspector]);
  const nodes = tree
    ? state.allFiles
      ? tree.nodes.filter((n) => n.kind === "file").sort(compareNodes)
      : (index?.children.get(directoryId) ?? [])
    : [];
  return (
    <div className="hp-workspace-screen">
      {state.notice && (
        <p role="status">
          {state.notice}
          <button onClick={() => useWorkspace.setState({ notice: "" })}>知道了</button>
        </p>
      )}
      <nav aria-label="空间路径" className="hp-breadcrumbs">
        {tree &&
          ancestors(tree, directoryId).map((n) => (
            <button
              key={n.node_id}
              onClick={() => {
                useShell.getState().requestResourceChange(() => {
                  useShell.getState().navigate({ screen: "workspace", directoryId: n.node_id });
                  if (!useShell.getState().pendingRoute)
                    useWorkspace.setState({ allFiles: false, searchMode: false, notice: "" });
                });
              }}
            >
              {n.parent_id === null ? "根目录" : n.name}
            </button>
          ))}
        {state.allFiles && <span>全部文件</span>}
      </nav>
      <div className="hp-workspace-toolbar">
        <button disabled={!canUpload || !tree} onClick={() => setUpload(true)}>
          上传文件
        </button>
        {!canUpload && <span>当前账户未启用文件上传</span>}
        <button
          onClick={() => {
            state.invalidate();
            setNotice("已请求刷新空间。");
          }}
        >
          刷新空间
        </button>
      </div>
      <form
        aria-label="长期文件搜索"
        className="hp-workspace-search"
        onSubmit={(e) => {
          e.preventDefault();
          setSearchMode(true);
          void state.searchFiles(filters);
        }}
      >
        <label>
          全空间文件名称
          <input
            aria-label="长期文件名称"
            value={filters.name ?? ""}
            onChange={(e) => setFilters({ ...filters, name: e.target.value })}
          />
        </label>
        <button disabled={state.searching}>搜索</button>
        {searchMode && (
          <button type="button" onClick={() => setSearchMode(false)}>
            返回目录
          </button>
        )}
        <details>
          <summary>高级搜索</summary>
          {(
            [
              ["summary", "摘要关键词"],
              ["content_type", "内容类型"],
              ["work_id", "来源 Work ID"],
              ["source_run_id", "来源 Run ID"],
              ["from_date", "开始创建日期"],
              ["to_date", "结束创建日期"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                aria-label={label}
                type={key.endsWith("date") ? "date" : "text"}
                value={filters[key] ?? ""}
                onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}
              />
            </label>
          ))}
          <label>
            来源
            <select
              value={filters.purpose ?? ""}
              onChange={(e) => setFilters({ ...filters, purpose: e.target.value })}
            >
              <option value="">全部</option>
              <option value="input">上传</option>
              <option value="output">AI 输出</option>
            </select>
          </label>
        </details>
      </form>
      {state.error && (
        <p role="alert">
          {state.error}
          <button onClick={() => void state.loadTree(true).catch(() => {})}>重试空间</button>
        </p>
      )}
      {!tree && !state.error && <p role="status">正在加载空间…</p>}
      {searchMode ? (
        <section aria-label="长期文件搜索结果">
          {state.searching && <p role="status">正在搜索全空间文件…</p>}
          {state.searchError && (
            <p role="alert">
              {state.searchError}
              <button
                onClick={() =>
                  void state.searchFiles(state.filters, Boolean(state.search?.next_after))
                }
              >
                重试搜索
              </button>
            </p>
          )}
          {state.searchStale && (
            <p role="status">
              结果已变化，请刷新。
              <button onClick={() => void state.searchFiles(state.filters)}>刷新搜索</button>
            </p>
          )}
          {state.search && <FileList nodes={[]} search={state.search.items} />}
          {state.search?.items.length === 0 && <p>没有匹配文件。</p>}
          {state.search?.next_after && (
            <button
              disabled={state.searching || state.searchStale}
              onClick={() => void state.searchFiles(undefined, true)}
            >
              加载更多搜索结果
            </button>
          )}
        </section>
      ) : (
        <>
          {tree && <FileList nodes={nodes} />}
          {tree && nodes.length === 0 && <p>此处暂无文件或目录。</p>}
          <form
            aria-label="新建子目录"
            className="hp-operation-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (busy) return;
              setBusy(true);
              setError("");
              const generation = state.generation;
              void workspaceApi
                .createWorkspaceDirectory(directoryId, folder.trim())
                .then(() => {
                  if (generation !== useWorkspace.getState().generation) return;
                  state.invalidate();
                  setFolder("");
                  setNotice("目录已创建。");
                })
                .catch((err) => {
                  if (generation === useWorkspace.getState().generation)
                    setError(commandError(err));
                })
                .finally(() => {
                  if (generation === useWorkspace.getState().generation) setBusy(false);
                });
            }}
          >
            <label>
              新目录名称
              <input
                aria-label="新目录名称"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                disabled={busy}
              />
            </label>
            <button disabled={busy || !folder.trim() || !directoryId}>新建目录</button>
          </form>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {space.data && (
        <p className="hp-muted">
          物理文件 {space.data.physical_files} 个 · {space.data.physical_bytes}{" "}
          bytes；移除入口不代表立即释放空间。
        </p>
      )}
      {space.error && (
        <p role="status">
          空间统计暂不可用。<button onClick={space.retry}>重试统计</button>
        </p>
      )}
      {upload && (
        <UploadToWorkspaceDialog directoryId={directoryId} onClose={() => setUpload(false)} />
      )}
    </div>
  );
}
