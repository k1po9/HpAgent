import { describe, expect, it } from "vitest";
import { deriveVitality, PROGRESS_FRESH_MS, type ApprovalFact } from "./policy";
import type { HpRunStatus } from "../../api/types";
const run = { run_id: "R", status: "running" as HpRunStatus };
const now = 100_000;
const progress = (phase: string) => ({
  phase,
  summary: "阶段提示",
  scope: { runId: "R", generation: 7, receivedAt: now },
});
const ctx = (phase: string) => ({ now, generation: 7, progress: progress(phase) });
describe("vitality facts", () => {
  it.each([
    ["calling_model", "thinking"],
    ["planning", "thinking"],
    ["replanning", "thinking"],
    ["synthesizing", "thinking"],
    ["selecting_tools", "tool"],
    ["executing_tool", "tool"],
    ["waiting_for_account_execution", "waiting"],
    ["finalizing", "running"],
    ["future_phase", "running"],
  ])("maps trusted %s to %s", (phase, state) =>
    expect(deriveVitality(run, ctx(phase)).state).toBe(state),
  );
  it.each(["succeeded", "failed", "cancelled", "cancelling", "queued"] as const)(
    "%s overrides cached phase and approval",
    (status) => {
      const v = deriveVitality(
        { ...run, status },
        { ...ctx("calling_model"), approvals: "pending" },
      );
      expect(v.state).toBe(status === "queued" ? "waiting" : status);
      expect(v.phase).toBeNull();
      if (status === "queued") expect(v.caption).toBe("等待执行资源");
    },
  );
  it.each(["unknown", "none", "pending"] as ApprovalFact[])(
    "keeps approval %s explicit",
    (approvals) => {
      const v = deriveVitality(run, { ...ctx("calling_model"), approvals });
      expect(v.approvals).toBe(approvals);
      expect(v.state).toBe(approvals === "pending" ? "waiting" : "thinking");
    },
  );
  it.each(["wrongRun", "wrongGeneration", "missingScope", "expired", "future", "degraded"])(
    "ignores %s phase without inferring failure",
    (reason) => {
      const c = ctx("executing_tool");
      if (reason === "wrongRun") c.progress.scope.runId = "OTHER";
      if (reason === "wrongGeneration") c.generation++;
      if (reason === "missingScope") Object.assign(c.progress, { scope: undefined });
      if (reason === "expired") c.now += PROGRESS_FRESH_MS;
      if (reason === "future") c.now--;
      const v = deriveVitality(run, { ...c, degraded: reason === "degraded" });
      expect(v.state).toBe("running");
      expect(v.phase).toBeNull();
      expect(v.terminal).toBe(false);
    },
  );
  it("history has no invented phase", () => expect(deriveVitality(run, { now }).phase).toBeNull());
});
