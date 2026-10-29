import { Alert, Breadcrumb, Button, Input, SearchBar } from "@lobehub/ui";
import { WorkspaceDisclosure, WorkspaceSelectField } from "./WorkspaceControls";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  const draftVersion = useRef(0);
  const discardDirectory = useCallback(() => {
    draftVersion.current++;
    setFolder("");
  }, []);
  useEffect(() => {
    if (!folder) return;
    useShell.setState({ dirtyDirectoryEditor: discardDirectory });
    return () => {
      if (useShell.getState().dirtyDirectoryEditor === discardDirectory)
        useShell.setState({ dirtyDirectoryEditor: null });
    };
  }, [folder, discardDirectory]);
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
    <div className="hp-workspace-screen hp-workspace-screen--lobe hp-lobe-scope">
      {state.notice && (
        <p role="status">
          {state.notice}
          <Button onClick={() => useWorkspace.setState({ notice: "" })}>知道了</Button>
        </p>
      )}
      <Breadcrumb
        aria-label="空间路径"
        className="hp-breadcrumbs"
        items={
          tree
            ? [
                ...ancestors(tree, directoryId).map((node) => ({
                  key: node.node_id,
                  title: node.parent_id === null ? "根目录" : node.name,
                  onClick: () =>
                    useShell.getState().requestResourceChange(() => {
                      if (
                        useShell
                          .getState()
                          .navigate({ screen: "workspace", directoryId: node.node_id })
                      )
                        useWorkspace.setState({ allFiles: false, searchMode: false, notice: "" });
                    }),
                })),
                ...(state.allFiles ? [{ key: "all-files", title: "全部文件" }] : []),
              ]
            : []
        }
      />
      <div className="hp-workspace-toolbar">
        <Button type="primary" disabled={!canUpload || !tree} onClick={() => setUpload(true)}>
          上传文件
        </Button>
        {!canUpload && <span>当前账户未启用文件上传</span>}
        <Button
          onClick={() => {
            state.invalidate();
            setNotice("已请求刷新空间。");
          }}
        >
          刷新空间
        </Button>
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
          <SearchBar
            aria-label="长期文件名称"
            enableShortKey={false}
            loading={state.searching}
            placeholder="搜索全空间文件"
            value={filters.name ?? ""}
            onChange={(e) => setFilters({ ...filters, name: e.target.value })}
          />
        </label>
        <Button htmlType="submit" loading={state.searching} disabled={state.searching}>
          搜索
        </Button>
        {searchMode && (
          <Button htmlType="button" onClick={() => setSearchMode(false)}>
            返回目录
          </Button>
        )}
        <WorkspaceDisclosure title="高级搜索" className="hp-workspace-filters">
          <div className="hp-workspace-filter-grid">
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
                <Input
                  aria-label={label}
                  type={key.endsWith("date") ? "date" : "text"}
                  value={filters[key] ?? ""}
                  onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}
                />
              </label>
            ))}
            <WorkspaceSelectField
              label="来源"
              value={filters.purpose ?? ""}
              options={[
                { value: "", label: "全部" },
                { value: "input", label: "上传" },
                { value: "output", label: "AI 输出" },
              ]}
              onChange={(value) => setFilters({ ...filters, purpose: value })}
            />
          </div>
        </WorkspaceDisclosure>
      </form>
      {state.error && (
        <p role="alert">
          {state.error}
          <Button onClick={() => void state.loadTree(true).catch(() => {})}>重试空间</Button>
        </p>
      )}
      {!tree && !state.error && <p role="status">正在加载空间…</p>}
      {searchMode ? (
        <section aria-label="长期文件搜索结果">
          {state.searching && <p role="status">正在搜索全空间文件…</p>}
          {state.searchError && (
            <p role="alert">
              {state.searchError}
              <Button
                onClick={() =>
                  void state.searchFiles(state.filters, Boolean(state.search?.next_after))
                }
              >
                重试搜索
              </Button>
            </p>
          )}
          {state.searchStale && (
            <p role="status">
              结果已变化，请刷新。
              <Button onClick={() => void state.searchFiles(state.filters)}>刷新搜索</Button>
            </p>
          )}
          {state.search && <FileList nodes={[]} search={state.search.items} />}

          {state.search?.next_after && (
            <Button
              disabled={state.searching || state.searchStale}
              onClick={() => void state.searchFiles(undefined, true)}
            >
              加载更多搜索结果
            </Button>
          )}
        </section>
      ) : (
        <>
          {tree && <FileList nodes={nodes} />}

          <form
            aria-label="新建子目录"
            className="hp-operation-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (busy) return;
              setBusy(true);
              setError("");
              const generation = state.generation;
              const version = draftVersion.current;
              void workspaceApi
                .createWorkspaceDirectory(directoryId, folder.trim())
                .then(() => {
                  if (generation !== useWorkspace.getState().generation) return;
                  state.invalidate();
                  if (version === draftVersion.current) {
                    setFolder("");
                    setNotice("目录已创建。");
                  }
                })
                .catch((err) => {
                  if (
                    generation === useWorkspace.getState().generation &&
                    version === draftVersion.current
                  )
                    setError(commandError(err));
                })
                .finally(() => {
                  if (generation === useWorkspace.getState().generation) setBusy(false);
                });
            }}
          >
            <label>
              新目录名称
              <Input
                aria-label="新目录名称"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                disabled={busy}
              />
            </label>
            <Button
              htmlType="submit"
              loading={busy}
              disabled={busy || !folder.trim() || !directoryId}
            >
              {busy ? "正在创建目录…" : "新建目录"}
            </Button>
          </form>
        </>
      )}
      {error && <Alert type="error" title={error} />}
      {notice && <Alert type="info" role="status" title={notice} />}
      {space.data && (
        <p className="hp-muted">
          物理文件 {space.data.physical_files} 个 · {space.data.physical_bytes}{" "}
          bytes；移除入口不代表立即释放空间。
        </p>
      )}
      {space.error && (
        <p role="status">
          空间统计暂不可用。<Button onClick={space.retry}>重试统计</Button>
        </p>
      )}
      {upload && (
        <UploadToWorkspaceDialog directoryId={directoryId} onClose={() => setUpload(false)} />
      )}
    </div>
  );
}
