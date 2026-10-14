import { useLayoutEffect, useState } from "react";
import type { HpRunStatus } from "../../api/types";
import type { RunProgress } from "../../sse/runFeed";
import { useRunInspector } from "../../store/runInspector";
import { deriveVitality, PROGRESS_FRESH_MS, type ApprovalFact } from "./policy";
export function useApprovalFact(runId: string | undefined): ApprovalFact {
  return useRunInspector((s) =>
    !runId || s.runId !== runId || !s.approvalLoaded || s.approvalError || s.unavailable
      ? "unknown"
      : s.approvals.some((a) => a.status === "pending")
        ? "pending"
        : "none",
  );
}
export function useVitality(
  run: { run_id: string; status: HpRunStatus } | null,
  progress: RunProgress | null,
  generation: number | undefined,
  degraded: boolean,
) {
  const approvals = useApprovalFact(run?.run_id);
  const [now, setNow] = useState(() => Date.now());
  useLayoutEffect(() => {
    if (!progress?.scope || run?.status !== "running") return;
    // Sample on fact reception and once on expiry; no animation-frame store clock.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNow(Date.now());
    const expires = progress.scope.receivedAt + PROGRESS_FRESH_MS;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timer);
  }, [progress, run?.run_id, run?.status, generation]);
  return deriveVitality(run, { progress, generation, degraded, approvals, now });
}
