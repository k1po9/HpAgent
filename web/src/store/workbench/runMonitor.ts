import { openRunFeed, type RunFeed } from "../../sse/runFeed";
import { useTraceStore } from "../../components/trace/traceStore";
import type { WorkbenchContext } from "./context";
import { isTerminalRunStatus } from "./runStatus";
import { reconcileAssistantMessage, appendDelta } from "./messages";

const POLL_DELAYS = [1000, 2000, 3000, 5000] as const;
export function createRunMonitor(context: WorkbenchContext) {
  const { api, set, get, deps, fence } = context;
  /** The live SSE feed for the current Run, if any; closed on switch/stop. */
  let activeFeed: RunFeed | null = null;

  let budgetRefreshTimer: ReturnType<typeof setTimeout> | null = null;

  const pendingSleeps = new Map<ReturnType<typeof setTimeout>, () => void>();

  const pause = (ms: number) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        pendingSleeps.delete(timer);
        resolve();
      }, ms);
      pendingSleeps.set(timer, resolve);
    });

  const closeFeed = (): void => {
    if (activeFeed) {
      activeFeed.close();
      activeFeed = null;
    }
    if (budgetRefreshTimer) {
      clearTimeout(budgetRefreshTimer);
      budgetRefreshTimer = null;
    }
  };

  async function refreshRunBudgetOnly(runId: string, generation: number): Promise<void> {
    try {
      const snapshot = await api.getRun(runId);
      if (snapshot.source_kind !== "chat") return;
      const latest = get();
      if (latest.pollGeneration !== generation || latest.activeRun?.run_id !== runId) return;
      set({ activeRun: { ...latest.activeRun, budget: snapshot.run.budget } });
    } catch {
      // Terminal confirmation or degraded polling will provide the next refresh.
    }
  }

  function scheduleBudgetRefresh(runId: string, generation: number, delay: number): void {
    if (budgetRefreshTimer) clearTimeout(budgetRefreshTimer);
    budgetRefreshTimer = setTimeout(() => {
      budgetRefreshTimer = null;
      void refreshRunBudgetOnly(runId, generation);
    }, delay);
  }

  /** Poll a Run until terminal; stops when a newer Run supersedes it. */
  function startPolling(runId: string): void {
    const generation = get().pollGeneration;
    void (async () => {
      let attempt = 0;
      for (;;) {
        const delay = POLL_DELAYS[Math.min(attempt, POLL_DELAYS.length - 1)] ?? 5000;
        await pause(delay);
        attempt += 1;
        const current = get();
        if (current.pollGeneration !== generation) return;
        if (current.activeRun?.run_id !== runId) return;
        if (current.activeRun && isTerminalRunStatus(current.activeRun.status)) {
          set({ polling: false, degraded: false });
          return;
        }
        try {
          const snapshot = await api.getRun(runId);
          if (snapshot.source_kind !== "chat") return;
          const latest = get();
          if (latest.pollGeneration !== generation) return;
          const run = snapshot.run;
          const terminal = isTerminalRunStatus(run.status);
          set({
            activeRun: run,
            messages: reconcileAssistantMessage(latest.messages, snapshot.assistant_message),
            ...(terminal ? { activeRunError: null } : {}),
            polling: !terminal,
            degraded: terminal ? false : get().degraded,
          });
          if (terminal) return;
        } catch {
          // Transient fetch error: keep polling with the current backoff.
        }
      }
    })();
  }

  /** One authoritative GET after a terminal SSE event (contract §12.4). */
  async function confirmRun(runId: string, generation: number): Promise<void> {
    try {
      const snapshot = await api.getRun(runId);
      if (snapshot.source_kind !== "chat") return;
      const latest = get();
      if (latest.pollGeneration !== generation || latest.activeRun?.run_id !== runId) return;
      set({
        activeRun: snapshot.run,
        messages: reconcileAssistantMessage(latest.messages, snapshot.assistant_message),
      });
    } catch {
      // The SSE snapshot is already authoritative; nothing to correct.
    }
  }

  /**
   * Watch a Run live over SSE (E-06). Deltas stream into the pending Message;
   * progress lands in the run-status area; a terminal snapshot replaces the
   * buffer. On degraded/connect loss the feed stops permanently and recovery
   * queries the Run, polling while it stays active (contract §13.2).
   */
  function startRunMonitor(runId: string): void {
    useTraceStore.getState().followRun(runId);
    const generation = get().pollGeneration;
    closeFeed();
    const current = get();
    if (current.activeRun && isTerminalRunStatus(current.activeRun.status)) {
      set({ polling: false, degraded: false, activeRunProgress: null });
      return;
    }
    set({ polling: true, degraded: false, activeRunProgress: null });
    const feedNodeTypes = new Map<string, string>();
    const stale = (): boolean => {
      const latest = get();
      return latest.pollGeneration !== generation || latest.activeRun?.run_id !== runId;
    };
    try {
      const feed = openRunFeed(
        runId,
        {
          onSnapshot: (snapshot) => {
            if (stale()) return;
            // Only a terminal Run dismisses a `conversation_busy` notice;
            // while a foreign Run is still executing the notice stays up.
            const terminal = isTerminalRunStatus(snapshot.run.status);
            set({
              activeRun: snapshot.run,
              messages: reconcileAssistantMessage(get().messages, snapshot.assistant_message),
              ...(terminal ? { activeRunError: null } : {}),
            });
            if (terminal) {
              set({ polling: false, degraded: false, activeRunProgress: null });
            }
          },
          onDelta: (messageId, delta) => {
            if (stale()) return;
            set({ messages: appendDelta(get().messages, runId, messageId, delta) });
          },
          onProgress: (progress) => {
            if (stale()) return;
            set({
              activeRunProgress: {
                ...progress,
                scope: { runId, generation, receivedAt: Date.now() },
              },
            });
          },
          onStatus: (status) => {
            if (stale()) return;
            set((s) => (s.activeRun ? { activeRun: { ...s.activeRun, status } } : {}));
          },
          onTrace: (event) => {
            if (stale()) return;
            const trace = useTraceStore.getState();
            if (trace.runId === runId) trace.applyEvent(runId, event);
            if (event.nodeType) {
              if (feedNodeTypes.size >= 512)
                feedNodeTypes.delete(feedNodeTypes.keys().next().value!);
              feedNodeTypes.set(event.nodeId, event.nodeType);
            }
            if ((event.nodeType ?? feedNodeTypes.get(event.nodeId)) === "llm") {
              scheduleBudgetRefresh(runId, generation, event.action === "start" ? 150 : 0);
            }
          },
          onTerminal: (snapshot) => {
            if (stale()) return;
            // The committed snapshot overrides the volatile delta buffer, then
            // a single GET corrects any drift (contract §12.4 run.succeeded).
            set({
              activeRun: snapshot.run,
              messages: reconcileAssistantMessage(get().messages, snapshot.assistant_message),
              activeRunError: null,
              activeRunProgress: null,
              polling: false,
              degraded: false,
            });
            void confirmRun(runId, generation);
            const trace = useTraceStore.getState();
            void trace.refreshSelectedRun(runId);
            feedNodeTypes.clear();
          },
          onDegraded: () => {
            if (stale()) return;
            // Delta assembly is permanently stopped; recover by querying the
            // Run and, while active, polling with backoff (contract §13).
            set({ degraded: true, activeRunProgress: null });
            startPolling(runId);
          },
          onAuthExpired: () => {
            if (!stale()) deps.onAuthExpired?.();
          },
        },
        { fetchImpl: deps.fetchImpl },
      );
      activeFeed = feed;
      void feed.done.then(() => {
        if (activeFeed === feed) activeFeed = null;
      });
    } catch {
      // Synchronous construction failure (never expected): poll instead.
      set({ degraded: true });
      startPolling(runId);
    }
  }

  /** Re-fetch the active Run from the conversation detail (busy recovery). */
  async function refreshActiveRun(): Promise<void> {
    const valid = fence();
    const conversationId = get().activeConversationId;
    if (!conversationId) {
      set({ activeRun: null, activeRunError: null });
      return;
    }
    const generation = get().pollGeneration;
    try {
      const detail = await api.getConversationDetail(conversationId);
      if (!valid()) return;
      const active = detail.active_run;
      const current = get().activeRun;
      const sameRun = active && current?.run_id === active.run.run_id;
      if (generation !== get().pollGeneration && !sameRun) return;
      set((s) => ({
        activeRun:
          sameRun && current.version >= active.run.version ? current : (active?.run ?? null),
        // A `conversation_busy` message is deliberately preserved: learning
        // that a foreign Run is active is not a reason to hide the notice —
        // it clears when that Run reaches terminal (see the run monitor).
        activeRunProgress: sameRun ? s.activeRunProgress : null,
        degraded: sameRun ? s.degraded : false,
        pollGeneration: active && !sameRun ? s.pollGeneration + 1 : s.pollGeneration,
      }));
      if (active && !sameRun) {
        startRunMonitor(active.run.run_id);
      }
    } catch {
      // Keep whatever Run we had; a later poll or user action will re-sync.
    }
  }
  function clearSleeps() {
    pendingSleeps.forEach((resolve, timer) => {
      clearTimeout(timer);
      resolve();
    });
    pendingSleeps.clear();
  }
  return { closeFeed, startRunMonitor, refreshActiveRun, clearSleeps };
}
export type RunMonitor = ReturnType<typeof createRunMonitor>;
