import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Folder, ListTodo, UserRound, Menu } from "lucide-react";
import { useShell, type Screen } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { useArtifacts } from "../../store/artifacts";
import { useTraceStore } from "../trace/traceStore";
import { ConversationSidebar } from "../ConversationSidebar";
import { ChatPane } from "../ChatPane";
import { WorkspacePanel } from "../WorkspacePanel";
import { WorkPanel } from "../WorkPanel";
import { WorkCreateForm, WorkResourcePanel } from "../WorkManagement";
import { ResearchOutputs } from "../ResearchOutputs";
import { QQBindingPanel } from "../QQBindingPanel";
import { RegistrationQqGate } from "../RegistrationQqGate";
import { ArtifactsPage, SaveWorkspaceDialog } from "../TestPages";
import { InspectorHost } from "./InspectorHost";
import { Surface } from "./Surface";
import { NotificationInbox } from "./LegacyUtilities";
import { api } from "../../api/client";

const screens = [
  { id: "ai", label: "AI", icon: Bot },
  { id: "workspace", label: "空间", icon: Folder },
  { id: "tasks", label: "任务", icon: ListTodo },
] as const;
const ignoreDirectory = () => {};
export function AppShell() {
  const route = useShell((s) => s.route);
  const modal = useShell((s) => s.modal);
  const sidebarOpen = useShell((s) => s.sidebarOpen);
  const navigate = useShell((s) => s.navigate);
  const account = useAuth((s) => s.account);
  const identities = useAuth((s) => s.identities);
  const justRegistered = useAuth((s) => s.justRegistered);
  const check = useAuth((s) => s.check);
  const dismissHint = useAuth((s) => s.dismissRegistrationHint);
  const conversations = useWorkbench((s) => s.conversations);
  const hasMoreConversations = useWorkbench((s) => s.hasMoreConversations);
  const loadingMoreConversations = useWorkbench((s) => s.loadingMoreConversations);
  const conversationsError = useWorkbench((s) => s.conversationsError);
  const pendingRoute = useShell((s) => s.pendingRoute);
  const loading = useWorkbench((s) => s.loadingConversations);
  const creating = useWorkbench((s) => s.creatingConversation);
  const conversationId = useWorkbench((s) => s.activeConversationId);
  const activeRun = useWorkbench((s) => s.activeRun);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  const [startBinding, setStartBinding] = useState(false);
  const [saveSource, setSaveSource] = useState<
    { file_id: string; file_name: string } | { html: string; file_name: string } | null
  >(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    const restore = () =>
      useShell.getState().restore(window.location.hash, {
        conversationId: useWorkbench.getState().activeConversationId ?? undefined,
        artifactId: useArtifacts.getState().openArtifactId ?? undefined,
        runId: useTraceStore.getState().runId ?? undefined,
      });
    restore();
    window.addEventListener("hashchange", restore);
    window.addEventListener("popstate", restore);
    void useWorkbench.getState().loadConversations();
    return () => {
      window.removeEventListener("hashchange", restore);
      window.removeEventListener("popstate", restore);
    };
  }, []);
  useEffect(() => {
    if (route.screen !== "ai") {
      if (
        useWorkbench.getState().creatingConversation &&
        !useWorkbench.getState().activeConversationId
      )
        useWorkbench.getState().leaveConversation();
      return;
    }
    if (route.conversationId) void useWorkbench.getState().selectConversation(route.conversationId);
    else if (useWorkbench.getState().activeConversationId)
      useWorkbench.getState().leaveConversation();
  }, [route.screen, route.conversationId]);
  const saveFile = useCallback(
    (file: { file_id: string; file_name: string }) => setSaveSource(file),
    [],
  );
  const workspaceProps = {
    accountId: account?.account_id ?? null,
    conversationId,
    currentRunId: activeRun?.run_id ?? null,
    candidateRunId:
      activeRun && ["queued", "running"].includes(activeRun.status) ? activeRun.run_id : null,
    currentRunStatus: activeRun?.status ?? null,
    refreshSignal: refresh,
    onSelectDirectory: ignoreDirectory,
  };
  function go(screen: Screen) {
    navigate({
      ...useShell.getState().pages[screen],
      screen,
      ...(screen === "ai" && conversationId ? { conversationId } : {}),
    });
  }
  const sidebar =
    route.screen === "ai" ? (
      <ConversationSidebar
        conversations={conversations}
        activeConversationId={conversationId}
        loading={loading}
        creating={creating}
        onSelect={(id) => {
          const current = useShell.getState().route;
          const sameConversationRoute = current.screen === "ai" && current.conversationId === id;
          navigate({ screen: "ai", conversationId: id });
          // A failed load clears the active ID, but leaves this route selected.
          // The store still deduplicates re-selection while loading or ready.
          if (sameConversationRoute) void useWorkbench.getState().selectConversation(id);
        }}
        hasMore={hasMoreConversations}
        loadingMore={loadingMoreConversations}
        error={conversationsError}
        onLoadMore={() => void useWorkbench.getState().loadMoreConversations()}
        onRefresh={() => void useWorkbench.getState().loadConversations()}
        onCreate={() => navigate({ screen: "ai" })}
      />
    ) : (
      <div className="hp-context-placeholder">
        <h2>{route.screen === "workspace" ? "空间" : "任务"}</h2>
        <p>{route.screen === "workspace" ? "文件与目录" : "持续工作"}</p>
        <p className="hp-muted">
          {route.screen === "workspace"
            ? "在主区域浏览和管理已有文件。"
            : "在主区域管理任务、研究报告与收件箱。"}
        </p>
      </div>
    );
  return (
    <div className={`hp-shell ${route.inspector ? "hp-shell--inspecting" : ""}`}>
      <div className="hp-rail">
        <span className="hp-brand" aria-label="HpAgent">
          H
        </span>
        <nav aria-label="主导航">
          {screens.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              aria-label={label}
              title={label}
              aria-current={route.screen === id ? "page" : undefined}
              onClick={() => go(id)}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <button
          className="hp-account-button"
          title="账户设置"
          aria-label="账户设置"
          onClick={() => useShell.setState({ modal: "account", sidebarOpen: false })}
        >
          <UserRound aria-hidden="true" />
        </button>
      </div>
      <aside className="hp-context-sidebar" aria-label="上下文导航">
        {sidebar}
      </aside>
      <main className="hp-main-canvas">
        <header className="hp-canvas-header">
          <button
            className="hp-sidebar-toggle"
            title="打开侧栏"
            aria-label="打开侧栏"
            onClick={() => useShell.setState({ sidebarOpen: true })}
          >
            <Menu aria-hidden="true" />
          </button>
          <h1 id="canvas-title" tabIndex={-1}>
            {screens.find((s) => s.id === route.screen)?.label}
          </h1>
          {route.screen === "ai" && conversationId && (
            <button onClick={() => useShell.setState({ modal: "artifact-create" })}>
              从回复生成 HTML
            </button>
          )}
          {route.screen === "ai" && conversationId && (
            <button onClick={() => useShell.setState({ modal: "resources" })}>对话资料</button>
          )}
        </header>
        {notice && <p role="status">{notice}</p>}
        <section hidden={route.screen !== "ai"} className="hp-shell-chat" aria-label="对话页面">
          {route.screen === "ai" && route.conversationId && !conversationId && !loading ? (
            <div role="alert">
              <p>{useWorkbench.getState().error ?? "对象不可用。"}</p>
              <button
                onClick={() =>
                  void useWorkbench.getState().selectConversation(route.conversationId!)
                }
              >
                重试对话
              </button>
              <button onClick={() => navigate({ screen: "ai" })}>返回新对话</button>
            </div>
          ) : (
            <ChatPane onSaveFile={saveFile} resourceRefresh={refresh} />
          )}
        </section>
        <section
          hidden={route.screen !== "workspace"}
          className="hp-screen-scroll"
          aria-label="空间页面"
        >
          <WorkspacePanel {...workspaceProps} view="files" directoryId={route.directoryId} />
        </section>
        <section
          hidden={route.screen !== "tasks"}
          className="hp-screen-scroll"
          aria-label="任务页面"
        >
          <WorkCreateForm conversationId={conversationId} research={route.type === "research"} />
          <WorkPanel conversationId={conversationId} pageMode />
          <details>
            <summary>工作资料授权</summary>
            <WorkResourcePanel />
          </details>
          <ResearchOutputs onSaveFile={saveFile} />
          <RunLookup />
          <NotificationInbox />
        </section>
      </main>
      <InspectorHost
        onSaveFile={saveFile}
        onSaveHtml={(html, name) => setSaveSource({ html, file_name: `${name}.txt` })}
      />
      {sidebarOpen && (
        <Surface
          title="上下文导航"
          className="hp-sidebar-drawer"
          onClose={() => useShell.setState({ sidebarOpen: false })}
        >
          {sidebar}
        </Surface>
      )}
      {pendingRoute && (
        <Surface title="放弃本轮附件？" onClose={() => useShell.setState({ pendingRoute: null })}>
          <p>切换对话将放弃本轮待发送附件。已有文件只取消选择。</p>
          <button onClick={() => useShell.setState({ pendingRoute: null })}>继续当前对话</button>
          <button
            onClick={() => {
              const state = useWorkbench.getState();
              const target = pendingRoute;
              useShell.setState({ pendingRoute: null });
              void Promise.all(
                state.attachments.map((a) => state.removeAttachment(a.localId)),
              ).then(() => {
                if (alive.current) useShell.getState().navigate(target);
              });
            }}
          >
            放弃本轮附件并切换
          </button>
        </Surface>
      )}
      {modal === "account" && (
        <Surface title="账户设置" onClose={() => useShell.setState({ modal: null })}>
          <p>登录身份：{identities?.web?.username ?? account?.account_id}</p>
          <QQBindingPanel qq={identities?.qq} onCompleted={check} startRequested={startBinding} />
          <button
            onClick={() => {
              void useAuth
                .getState()
                .signOut()
                .catch(() => {
                  /* Local session is already disposed; /me will verify on next entry. */
                });
            }}
          >
            退出登录
          </button>
        </Surface>
      )}
      {modal === "artifact-create" && (
        <Surface title="从回复生成 HTML" onClose={() => useShell.setState({ modal: null })}>
          <ArtifactsPage showPanel={false} />
        </Surface>
      )}
      {modal === "resources" && (
        <Surface
          title="当前对话资料"
          onClose={() => {
            useShell.setState({ modal: null });
            setRefresh((v) => v + 1);
          }}
        >
          <WorkspacePanel {...workspaceProps} view="authority" />
        </Surface>
      )}
      <RegistrationQqGate
        open={justRegistered && !identities?.qq.bound}
        onBind={() => {
          dismissHint();
          setStartBinding(true);
          useShell.setState({ modal: "account" });
        }}
        onSkip={dismissHint}
      />
      {saveSource && (
        <SaveWorkspaceDialog
          file={saveSource}
          onClose={() => setSaveSource(null)}
          onSaved={() => {
            if (alive.current) {
              setRefresh((v) => v + 1);
              setNotice("已保存到空间。");
            }
          }}
        />
      )}
    </div>
  );
}
function RunLookup() {
  const [runs, setRuns] = useState<Array<{ run_id: string; status: string }>>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState<{ workId: string; revision: number } | null>(null);
  useEffect(() => {
    if (!query) return;
    let valid = true;
    void api
      .request<{ items: typeof runs }>({
        method: "GET",
        path: `/api/v1/works/${encodeURIComponent(query.workId)}/runs`,
      })
      .then((page) => {
        if (valid) setRuns(page.items);
      })
      .catch(() => {
        if (valid) setError("执行记录暂不可用。");
      });
    return () => {
      valid = false;
    };
  }, [query]);
  return (
    <details>
      <summary>执行记录与诊断</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const value = new FormData(event.currentTarget).get("work");
          const workId = typeof value === "string" ? value.trim() : "";
          if (!workId) {
            setError("请输入工作编号。");
            return;
          }
          setError("");
          setRuns([]);
          setQuery((previous) => ({ workId, revision: (previous?.revision ?? 0) + 1 }));
        }}
      >
        <label>
          工作编号
          <input name="work" required />
        </label>
        <button>读取执行记录</button>
      </form>
      {runs.map((run) => (
        <button
          key={run.run_id}
          onClick={() => useShell.getState().openInspector({ kind: "run", objectId: run.run_id })}
        >
          {run.status} · {run.run_id}
        </button>
      ))}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const value = new FormData(event.currentTarget).get("run");
          if (typeof value === "string")
            useShell.getState().openInspector({ kind: "run", objectId: value.trim() });
        }}
      >
        <label>
          执行编号
          <input name="run" required />
        </label>
        <button>查询执行</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
