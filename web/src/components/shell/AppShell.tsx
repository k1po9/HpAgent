import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Folder, ListTodo, UserRound, Menu } from "lucide-react";
import { useShell, type Screen } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { useTraceStore } from "../trace/traceStore";
import { ConversationSidebar } from "../ConversationSidebar";
import { ChatPane } from "../ChatPane";
import { WorkspaceScreen } from "../workspace/WorkspaceScreen";
import { WorkspaceSidebar } from "../workspace/WorkspaceSidebar";
import { ResourceManager } from "../workspace/ResourceManager";
import { TaskScreen } from "../tasks/TaskScreen";
import { TaskSidebar } from "../tasks/TaskSidebar";
import { TaskEditor } from "../tasks/TaskEditor";
import { TaskInbox } from "../tasks/TaskInbox";
import { useTaskController } from "../tasks/useTaskController";
import { QQBindingPanel } from "../QQBindingPanel";
import { RegistrationQqGate } from "../RegistrationQqGate";
import { ArtifactsPage } from "../TestPages";
import { SaveToWorkspaceDialog as SaveWorkspaceDialog } from "../workspace/SaveToWorkspaceDialog";
import type { SaveSource } from "../workspace/workspaceOperations";
import { InspectorHost } from "./InspectorHost";
import { Surface } from "./Surface";

const screens = [
  { id: "ai", label: "AI", icon: Bot },
  { id: "workspace", label: "空间", icon: Folder },
  { id: "tasks", label: "任务", icon: ListTodo },
] as const;
export function AppShell() {
  useTaskController();
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
  const pendingResourceChange = useShell((s) => s.pendingResourceChange);
  const pendingReason = useShell((s) => s.pendingRouteReason);
  const loading = useWorkbench((s) => s.loadingConversations);
  const creating = useWorkbench((s) => s.creatingConversation);
  const conversationId = useWorkbench((s) => s.activeConversationId);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  const [startBinding, setStartBinding] = useState(false);
  const [saveSource, setSaveSource] = useState<SaveSource | null>(null);
  const [savedNode, setSavedNode] = useState<{ nodeId: string; parentId: string } | null>(null);
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
        artifactId:
          useShell.getState().route.inspector?.kind === "artifact"
            ? useShell.getState().route.inspector?.objectId
            : undefined,
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
  function go(screen: Screen) {
    if (screen === useShell.getState().route.screen) {
      useShell.setState({ sidebarOpen: false });
      return;
    }
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
    ) : route.screen === "workspace" ? (
      <WorkspaceSidebar />
    ) : (
      <TaskSidebar />
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
        {notice && (
          <p role="status">
            {notice}{" "}
            {savedNode && (
              <button
                onClick={() =>
                  navigate({
                    screen: "workspace",
                    directoryId: savedNode.parentId,
                    inspector: { kind: "file", objectId: savedNode.nodeId },
                  })
                }
              >
                打开空间
              </button>
            )}
          </p>
        )}
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
          {route.screen === "workspace" && <WorkspaceScreen />}
        </section>
        <section
          hidden={route.screen !== "tasks"}
          className="hp-screen-scroll"
          aria-label="任务页面"
        >
          {route.screen === "tasks" && <TaskScreen />}
        </section>
      </main>
      {modal !== "task-inbox" && <InspectorHost onSaveFile={saveFile} onSaveHtml={setSaveSource} />}
      {modal === "task-create" && <TaskEditor onClose={() => useShell.setState({ modal: null })} />}
      {modal === "task-inbox" && <TaskInbox onClose={() => useShell.setState({ modal: null })} />}
      {pendingRoute && pendingReason === "tasks" && (
        <Surface
          title="放弃未提交的任务草稿？"
          onClose={() => useShell.setState({ pendingRoute: null })}
        >
          <p>草稿尚未提交，切换将丢弃修改。</p>
          <button onClick={() => useShell.setState({ pendingRoute: null })}>继续编辑</button>
          <button
            onClick={() => {
              useShell.setState({ dirtyTaskEditor: null });
              useShell.getState().navigate(pendingRoute);
            }}
          >
            放弃草稿并继续
          </button>
        </Surface>
      )}
      {sidebarOpen && (
        <Surface
          title="上下文导航"
          className="hp-sidebar-drawer"
          onClose={() => useShell.setState({ sidebarOpen: false })}
        >
          {sidebar}
        </Surface>
      )}
      {((pendingRoute && pendingReason === "resources") || pendingResourceChange) && (
        <Surface
          title="放弃未提交的权限编辑？"
          onClose={() => useShell.setState({ pendingRoute: null, pendingResourceChange: null })}
        >
          <p>尚未提交的权限选择会丢弃，已生效授权不受影响。</p>
          <button
            onClick={() => useShell.setState({ pendingRoute: null, pendingResourceChange: null })}
          >
            继续编辑
          </button>
          <button
            onClick={() => {
              useShell.setState({
                dirtyResourceEditor: null,
                pendingRoute: null,
                pendingResourceChange: null,
              });
              if (pendingResourceChange) pendingResourceChange();
              else if (pendingRoute) useShell.getState().navigate(pendingRoute);
            }}
          >
            放弃编辑并继续
          </button>
        </Surface>
      )}
      {pendingRoute && pendingReason === "attachments" && (
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
            if (useShell.getState().dirtyResourceEditor) {
              useShell.getState().navigate(useShell.getState().route);
              return;
            }
            useShell.setState({ modal: null });
            setRefresh((v) => v + 1);
          }}
        >
          <ResourceManager
            initialSubject={
              conversationId
                ? {
                    kind: "conversation",
                    id: conversationId,
                    title:
                      conversations.find((c) => c.conversation_id === conversationId)?.title ??
                      "当前对话",
                  }
                : undefined
            }
          />
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
          onSaved={(operation) => {
            if (alive.current) {
              setRefresh((v) => v + 1);
              setNotice(
                !(operation.source instanceof File) && "html" in operation.source
                  ? `源码副本已保存到空间：${operation.name}${operation.source.version ? `（v${operation.source.version}）` : ""}`
                  : "已保存到空间。",
              );
              if (operation.nodeId)
                setSavedNode({ nodeId: operation.nodeId, parentId: operation.parentId });
            }
          }}
        />
      )}
    </div>
  );
}
