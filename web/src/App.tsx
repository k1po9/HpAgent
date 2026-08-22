import { useEffect, useRef, useState } from "react";
import { Flex, Spinner, Text } from "@radix-ui/themes";
import { ChatPane } from "./components/ChatPane";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { LoginForm } from "./components/LoginForm";
import { useAuth } from "./store/auth";
import { useWorkbench } from "./store/workbench";
import { useArtifacts } from "./store/artifacts";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { RegistrationQqGate } from "./components/RegistrationQqGate";
import { TracePanel } from "./components/trace/TracePanel";
import { useTraceStore } from "./components/trace/traceStore";

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

  return <Workbench />;
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
  const account = useAuth((s) => s.account);
  const identities = useAuth((s) => s.identities);
  const justRegistered = useAuth((s) => s.justRegistered);
  const dismissRegistrationHint = useAuth((s) => s.dismissRegistrationHint);
  const refreshIdentity = useAuth((s) => s.check);
  const signOut = useAuth((s) => s.signOut);
  const loadConversations = useWorkbench((s) => s.loadConversations);
  const createConversation = useWorkbench((s) => s.createConversation);
  const selectConversation = useWorkbench((s) => s.selectConversation);
  const openArtifactId = useArtifacts((s) => s.openArtifactId);
  const resetArtifacts = useArtifacts((s) => s.reset);
  const [startQqBinding, setStartQqBinding] = useState(false);

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
      <Flex className="hp-workbench">
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
        <Flex direction="column" className="hp-chatpane">
          {activeConversationId ? <ChatPane /> : <EmptySelection />}
        </Flex>
        <TracePanel />
        {openArtifactId ? <ArtifactPanel /> : null}
      </Flex>
    </>
  );
}

function EmptySelection() {
  return (
    <Flex align="center" justify="center" style={{ height: "100%" }}>
      <Text size="3" color="gray">
        选择或新建一个对话开始。
      </Text>
    </Flex>
  );
}
