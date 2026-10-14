import { useEffect, useState, type RefObject } from "react";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { runApi } from "../../store/runInspector";
import type { HpRun } from "../../api/types";

/** Visible history cards read their own Run; no inferred status, global selection or retained account cache. */
export function useRunCard(runId: string, live: boolean, ref: RefObject<HTMLElement | null>) {
  const account = useAuth((s) => s.account);
  const conversation = useWorkbench((s) => s.activeConversationId);
  const [loaded, setLoaded] = useState<{
    account: typeof account;
    conversation: typeof conversation;
    id: string;
    run: HpRun;
  } | null>(null);
  useEffect(() => {
    if (live || !account || !ref.current) return;
    const controller = new AbortController();
    let valid = true,
      started = false,
      timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      if (started) return;
      started = true;
      timer = setTimeout(() => controller.abort(), 12000);
      void runApi
        .getRun(runId, controller.signal)
        .then((data) => {
          if (
            valid &&
            !controller.signal.aborted &&
            data.source_kind === "chat" &&
            data.run.run_id === runId &&
            data.run.conversation_id === conversation
          )
            setLoaded({ account, conversation, id: runId, run: data.run });
        })
        .catch(() => {})
        .finally(() => clearTimeout(timer));
    };
    const observer =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
              observer?.disconnect();
              load();
            }
          });
    if (observer) observer.observe(ref.current);
    else load();
    return () => {
      valid = false;
      controller.abort();
      clearTimeout(timer);
      observer?.disconnect();
    };
  }, [account, conversation, runId, live, ref]);
  const own =
    loaded?.account === account && loaded.conversation === conversation && loaded.id === runId
      ? loaded.run
      : null;
  return own;
}
