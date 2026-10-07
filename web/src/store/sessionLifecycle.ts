import { useRunInspector } from "./runInspector";
import { useConversationUi } from "./conversationUi";
import { useAuth } from "./auth";
import { useWorkbench } from "./workbench";
import { useWorks } from "./works";
import { useArtifacts } from "./artifacts";
import { useTraceStore } from "../components/trace/traceStore";
import { useShell } from "./shell";
import { disposeWorkFeeds } from "../sse/workFeed";
import { api } from "../api/client";

// Synchronous boundary: invalidate requests before React mounts another account.
export const disposeSessionLifecycle = useAuth.subscribe((state, previous) => {
  if (
    state.account?.account_id === previous.account?.account_id &&
    (state.status === "signedIn") === (previous.status === "signedIn")
  )
    return;
  if (previous.account) api.reset(state.status === "signedIn");
  disposeWorkFeeds();
  useWorkbench.getState().reset();
  useConversationUi.getState().reset();
  useWorks.getState().reset();
  useArtifacts.getState().reset();
  useTraceStore.getState().reset();
  useRunInspector.getState().reset();
  useShell.getState().reset();
  // Preserve deep links on first authentication, discard the previous account's IDs.
  if (previous.account && typeof window !== "undefined")
    window.history.replaceState(null, "", "#/ai");
});

if (import.meta.hot) import.meta.hot.dispose(disposeSessionLifecycle);
