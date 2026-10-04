import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Flex, Spinner, Text } from "@radix-ui/themes";
import { WorkPanel } from "./components/WorkPanel";
import { useWorks } from "./store/works";
import { ChatPane } from "./components/ChatPane";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { LoginForm } from "./components/LoginForm";
import { useAuth } from "./store/auth";
import { useWorkbench } from "./store/workbench";
import { useArtifacts } from "./store/artifacts";
import { ArtifactsPage, DiagnosticsPage, SaveWorkspaceDialog } from "./components/TestPages";
import { WorkCreateForm, WorkResourcePanel } from "./components/WorkManagement";
import { api } from "./api/client";
import { commandError } from "./utils/commands";
import { QQBindingPanel } from "./components/QQBindingPanel";
import { RegistrationQqGate } from "./components/RegistrationQqGate";
import { ResearchOutputs } from "./components/ResearchOutputs";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { useTraceStore } from "./components/trace/traceStore";
const ignoreDirectory = () => {};
const pages = {
  chat: "对话",
  files: "长期文件",
  authority: "资料授权",
  works: "持续工作",
  research: "研究",
  artifacts: "成果",
  diagnostics: "执行诊断",
  account: "账户",
} as const;
type Page = keyof typeof pages;
function initialPage(): Page {
  const value = window.location.hash.slice(1);
  return value in pages ? (value as Page) : "chat";
}

/**
 * Auth gate: probe `/api/v1/me` on mount; signed-in sessions open the chat
 * workbench (E-03), signed-out sessions show the login form. A mid-session
 * `auth.expired`/401 flips the store back here (contract API-015).
 */
export function App() {
  const status = useAuth((s) => s.status);
  const accountId = useAuth((s) => s.account?.account_id ?? null);
  const check = useAuth((s) => s.check);
  const resetArtifacts = useArtifacts((s) => s.reset);
  const resetTrace = useTraceStore((s) => s.reset);

  useEffect(() => {
    void check();
  }, [check]);

  useEffect(() => {
    resetArtifacts();
    resetTrace();
    useWorks.getState().reset();
  }, [status, accountId, resetArtifacts, resetTrace]);

  if (status === "checking") {
    return (
      <Flex align="center" justify="center" style={{ minHeight: "60vh" }} gap="2">
        <Spinner />
        <Text size="2" color="gray">
          正在恢复会话…
        </Text>
      </Flex>
    );
  }

  if (status === "signedOut") {
    return <LoginForm />;
  }

  if (status === "error") {
    return (
      <Flex align="center" justify="center" style={{ minHeight: "60vh" }} gap="3">
        <Text role="alert">无法连接 HpAgent API</Text>
        <Button onClick={() => void check()}>重试</Button>
      </Flex>
    );
  }

  return <Workbench key={accountId} />;
}

/**
 * Chat workbench (phase-e E-03/E-04).
 *
 * Sidebar owns the conversation list; the ChatPane owns the active thread and
 * the run-status strip. The store is the single source of truth — the view is
 * rebuilt from the API on refresh, never from an assistant-ui local cache.
 */
function Workbench() {
  const conversations = useWorkbench((s) => s.conversations);
  const conversationsLoaded = useWorkbench((s) => s.conversationsLoaded);
  const loadingConversations = useWorkbench((s) => s.loadingConversations);
  const creatingConversation = useWorkbench((s) => s.creatingConversation);
  const activeConversationId = useWorkbench((s) => s.activeConversationId);
  const activeRunId = useWorkbench((s) => s.activeRun?.run_id ?? null);
  const currentRunStatus = useWorkbench((s) => s.activeRun?.status ?? null);
  const candidateRunId = useWorkbench((s) =>
    s.activeRun && ["queued", "running"].includes(s.activeRun.status) ? s.activeRun.run_id : null,
  );
  const account = useAuth((s) => s.account);
  const identities = useAuth((s) => s.identities);
  const justRegistered = useAuth((s) => s.justRegistered);
  const dismissRegistrationHint = useAuth((s) => s.dismissRegistrationHint);
  const refreshIdentity = useAuth((s) => s.check);
  const signOut = useAuth((s) => s.signOut);
  const loadConversations = useWorkbench((s) => s.loadConversations);
  const createConversation = useWorkbench((s) => s.createConversation);
  const selectConversation = useWorkbench((s) => s.selectConversation);
  const resetArtifacts = useArtifacts((s) => s.reset);
  const [startQqBinding, setStartQqBinding] = useState(false);
  const [page, setPage] = useState<Page>(initialPage);
  const [workspaceRefresh, setWorkspaceRefresh] = useState(0);
  const [saveFile, setSaveFile] = useState<{ file_id: string; file_name: string } | null>(null);
  const [workspaceNotice, setWorkspaceNotice] = useState("");
  const [authoritySubject, setAuthoritySubject] = useState("conversation");
  const saveToWorkspace = useCallback((file: { file_id: string; file_name: string }) => {
    setSaveFile(file);
    setWorkspaceNotice("");
  }, []);
  const navigate = useCallback((next: Page) => {
    window.history.replaceState(null, "", `#${next}`);
    setPage(next);
  }, []);
  useEffect(() => {
    const update = () => setPage(initialPage());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(
    () =>
      useArtifacts.subscribe((state, previous) => {
        if (state.openArtifactId && state.openArtifactId !== previous.openArtifactId)
          navigate("artifacts");
      }),
    [navigate],
  );
  useEffect(
    () =>
      useTraceStore.subscribe((state, previous) => {
        if (state.open && !previous.open) navigate("diagnostics");
      }),
    [navigate],
  );
  const initialSelectionDone = useRef(false);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  // Auto-open the newest conversation once the list has loaded.
  useEffect(() => {
    if (!conversationsLoaded || initialSelectionDone.current) return;
    const first = conversations[0];
    if (!first) return;
    initialSelectionDone.current = true;
    void selectConversation(first.conversation_id);
  }, [conversationsLoaded, conversations, selectConversation]);

  return (
    <>
      <RegistrationQqGate
        open={justRegistered && !identities?.qq.bound}
        onBind={() => {
          setStartQqBinding(true);
          dismissRegistrationHint();
        }}
        onSkip={dismissRegistrationHint}
      />
      <div className="hp-test-workbench">
        <nav className="hp-page-nav" aria-label="功能页面">
          {Object.entries(pages).map(([id, label]) => (
            <button
              key={id}
              type="button"
              aria-current={page === id ? "page" : undefined}
              onClick={() => navigate(id as Page)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="hp-page-context">
          <label>
            当前对话{" "}
            <select
              aria-label="当前对话"
              value={activeConversationId ?? ""}
              onChange={(e) => {
                if (e.target.value) {
                  resetArtifacts();
                  void selectConversation(e.target.value);
                }
              }}
            >
              <option value="">尚未选择</option>
              {conversations.map((c) => (
                <option key={c.conversation_id} value={c.conversation_id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
          <span>文件和授权操作针对所选对话；工作权限单独管理。</span>
        </div>
        {workspaceNotice && <p role="status">{workspaceNotice}</p>}
        <Flex className="hp-workbench hp-page-body">
          <div hidden={page !== "chat"} className="hp-sidebar-host">
            <ConversationSidebar
              conversations={conversations}
              activeConversationId={activeConversationId}
              loading={loadingConversations}
              creating={creatingConversation}
              accountName={account?.account_id ?? "账号"}
              onSelect={(id) => {
                resetArtifacts();
                void selectConversation(id);
              }}
              onCreate={() => {
                resetArtifacts();
                void createConversation();
              }}
              onSignOut={() => {
                resetArtifacts();
                void signOut();
              }}
              qqIdentity={identities?.qq}
              onIdentityRefresh={refreshIdentity}
              startQqBinding={startQqBinding}
            />
          </div>
          <main className={`hp-page-content hp-page-content--${page}`}>
            <section
              hidden={page !== "chat"}
              className="hp-chatpane hp-chat-page"
              aria-label="对话页面"
            >
              {activeConversationId ? (
                <ChatPane
                  onSaveFile={saveToWorkspace}
                  resourceRefresh={workspaceRefresh + (page === "chat" ? 1 : 0)}
                />
              ) : (
                <EmptySelection />
              )}
            </section>
            <section
              hidden={
                page !== "files" && !(page === "authority" && authoritySubject === "conversation")
              }
            >
              <h1>{page === "authority" ? "当前对话资料授权" : "长期文件"}</h1>
              {page === "authority" && (
                <label>
                  授权对象{" "}
                  <select
                    value={authoritySubject}
                    onChange={(e) => setAuthoritySubject(e.target.value)}
                  >
                    <option value="conversation">当前对话</option>
                    <option value="work">工作</option>
                  </select>
                </label>
              )}
              <WorkspacePanel
                accountId={account?.account_id ?? null}
                currentRunId={activeRunId}
                candidateRunId={candidateRunId}
                currentRunStatus={currentRunStatus}
                conversationId={activeConversationId}
                refreshSignal={workspaceRefresh}
                onSelectDirectory={ignoreDirectory}
                view={page === "authority" ? "authority" : "files"}
              />
            </section>
            {page === "authority" && authoritySubject === "work" && (
              <section>
                <h1>工作资料授权</h1>
                <label>
                  授权对象{" "}
                  <select
                    value={authoritySubject}
                    onChange={(e) => setAuthoritySubject(e.target.value)}
                  >
                    <option value="conversation">当前对话</option>
                    <option value="work">工作</option>
                  </select>
                </label>
                <WorkResourcePanel />
              </section>
            )}
            <section hidden={page !== "works"}>
              {page === "works" && <WorkCreateForm conversationId={activeConversationId} />}
              <WorkPanel conversationId={activeConversationId} pageMode />
              {page === "works" && <NotificationInbox />}
            </section>
            {page === "research" && (
              <section>
                <WorkCreateForm conversationId={activeConversationId} research />
                <ResearchOutputs pageMode onSaveFile={saveToWorkspace} />
              </section>
            )}
            {page === "artifacts" && (
              <ArtifactsPage
                onWorkspaceSaved={() => {
                  setWorkspaceRefresh((v) => v + 1);
                  setWorkspaceNotice("成果已保存到长期目录。");
                }}
              />
            )}
            {page === "diagnostics" && <DiagnosticsPage />}
            {page === "account" && (
              <section className="hp-operation-form">
                <h1>账户</h1>
                <p>登录身份：{identities?.web?.username ?? account?.account_id}</p>
                <QQBindingPanel qq={identities?.qq} onCompleted={refreshIdentity} />
                <Button onClick={() => void signOut()}>退出登录</Button>
              </section>
            )}
          </main>
        </Flex>
      </div>
      {saveFile && (
        <SaveWorkspaceDialog
          key={saveFile.file_id}
          file={saveFile}
          onClose={() => setSaveFile(null)}
          onSaved={() => {
            setWorkspaceRefresh((v) => v + 1);
            setWorkspaceNotice("文件已保存到长期目录，可在资料授权页供对话或工作使用。");
          }}
        />
      )}
    </>
  );
}

function EmptySelection() {
  const error = useWorkbench((s) => s.error);
  const clearError = useWorkbench((s) => s.clearError);
  return (
    <Flex direction="column" gap="3" align="center" justify="center" style={{ height: "100%" }}>
      {error ? (
        <Flex gap="2" align="center">
          <Text color="red" role="alert">
            {error}
          </Text>
          <Button size="1" variant="soft" onClick={clearError}>
            关闭提示
          </Button>
        </Flex>
      ) : null}
      <Text size="3" color="gray">
        选择或新建一个对话开始。
      </Text>
    </Flex>
  );
}

function NotificationInbox() {
  const [items, setItems] = useState<Array<{ notification_id: string; content: unknown }>>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const page = await api.request<{ items: typeof items }>({
        method: "GET",
        path: "/api/v1/notifications",
      });
      setItems(page.items);
      setError(null);
    } catch (e) {
      setError(commandError(e));
    }
  }, []);
  useEffect(() => {
    let current = true;
    void Promise.resolve().then(() => {
      if (current) return refresh();
    });
    return () => {
      current = false;
    };
  }, [refresh]);
  return (
    <section className="hp-operation-form">
      <h2>账户收件箱</h2>
      <button onClick={() => void refresh()}>刷新收件箱</button>
      {!items.length && <p>暂无已送达提醒。</p>}
      {items.map((item) => (
        <pre key={item.notification_id}>
          {typeof item.content === "string" ? item.content : JSON.stringify(item.content, null, 2)}
        </pre>
      ))}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
