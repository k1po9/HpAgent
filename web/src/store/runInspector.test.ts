import { beforeEach, describe, expect, it, vi } from "vitest";
import { createRunInspector } from "./runInspector";
import { useWorkbench } from "./workbench";
import { HpCommandError, type HpFileApproval, type HpRunSnapshot } from "../api/types";
const approval: HpFileApproval = {
  approval_id: "approval",
  run_id: "A",
  operation_id: "op",
  action_summary: "写入文件",
  tool_name: "write",
  status: "pending",
  expires_at: "2099-01-01T00:00:00Z",
};
const work = (id: string): HpRunSnapshot => ({
  source_kind: "work",
  run: {
    source_kind: "work",
    run_id: id,
    execution_id: id,
    work_id: "work",
    requirement_revision: 1,
    work_control_epoch: 1,
    conversation_id: null,
    session_id: null,
    status: "running",
    version: 1,
    created_at: "2026-10-07T00:00:00Z",
    updated_at: "2026-10-07T00:00:01Z",
    started_at: "2026-10-07T00:00:01Z",
    finished_at: null,
    failure_code: null,
    failure_message: null,
    strategy_kind: "generic_agent",
    executor_key: "general",
    result_json: null,
    budget: null,
    published_file: null,
    branches: [],
  },
});
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => useWorkbench.getState().reset());
describe("Run Inspector isolation and approval intentions", () => {
  it("ignores A→B→A stale snapshots and account reset", async () => {
    const a = deferred<HpRunSnapshot>();
    const api = { getRun: vi.fn().mockReturnValueOnce(a.promise).mockResolvedValue(work("A")) };
    const store = createRunInspector(api as never);
    store.getState().select("A");
    const old = store.getState().load();
    store.getState().select("B");
    store.getState().select("A");
    await store.getState().load();
    a.resolve(work("stale"));
    await old;
    expect(store.getState().snapshot?.run.run_id).toBe("A");
    expect(useWorkbench.getState().activeRun).toBeNull();
    expect(api.getRun.mock.calls[0]?.[1]?.aborted).toBe(true);
    const b = deferred<HpRunSnapshot>();
    api.getRun.mockReturnValueOnce(b.promise);
    const late = store.getState().load();
    store.getState().reset();
    b.resolve(work("A"));
    await late;
    expect(store.getState().snapshot).toBeNull();
  });
  it("retains old snapshot for network errors and clears it on access denial", async () => {
    const api = {
      getRun: vi
        .fn()
        .mockResolvedValueOnce(work("A"))
        .mockRejectedValueOnce(new Error("offline"))
        .mockRejectedValueOnce(
          new HpCommandError(404, {
            code: "not_found",
            message: "missing",
            request_id: null,
            retryable: false,
            details: {},
          }),
        ),
    };
    const store = createRunInspector(api as never);
    store.getState().select("A");
    await store.getState().load();
    await store.getState().load();
    expect(store.getState().snapshot).not.toBeNull();
    await store.getState().load();
    expect(store.getState().snapshot).toBeNull();
    expect(store.getState().unavailable).toBe(true);
  });
  it("shares allow/reject lock and replays unknown decisions with the original key after readback", async () => {
    const post = deferred<{ approval: HpFileApproval }>();
    const api = {
      decideFileApproval: vi
        .fn()
        .mockReturnValueOnce(post.promise)
        .mockResolvedValue({ approval: { ...approval, status: "approved" } }),
      listRunFileApprovals: vi.fn().mockResolvedValue({ approvals: [approval] }),
      getRun: vi.fn().mockResolvedValue(work("A")),
    };
    const store = createRunInspector(api as never);
    store.getState().select("A");
    const first = store.getState().decide(approval, "approve");
    await store.getState().decide(approval, "reject");
    expect(api.decideFileApproval).toHaveBeenCalledTimes(1);
    // A dropped reply after server acceptance cannot create an opposite intention.
    store.getState().select("B");
    store.getState().select("A");
    expect(store.getState().intents.approval?.state).toBe("sending");
    store.getState().reset();
    post.resolve({ approval: { ...approval, status: "approved" } });
    await first;
    expect(store.getState().approvals).toEqual([]);
  });
  it("reads back a lost reply and only permits replay of the same decision/key", async () => {
    const api = {
      decideFileApproval: vi
        .fn()
        .mockRejectedValueOnce(new Error("lost response"))
        .mockResolvedValue({ approval: { ...approval, status: "approved" } }),
      listRunFileApprovals: vi.fn().mockResolvedValue({ approvals: [approval] }),
      getRun: vi.fn().mockResolvedValue(work("A")),
    };
    const store = createRunInspector(api as never);
    store.getState().select("A");
    await store.getState().decide(approval, "approve");
    const key = api.decideFileApproval.mock.calls[0]?.[2];
    store.getState().select("B");
    store.getState().select("A");
    await store.getState().loadApprovals();
    await store.getState().decide(approval, "reject", true);
    expect(api.decideFileApproval).toHaveBeenCalledTimes(1);
    await store.getState().decide(approval, "approve", true);
    expect(api.decideFileApproval.mock.calls[1]?.[2]).toBe(key);
  });
  it("does not let a pre-command read restore pending or an old button reverse a decision", async () => {
    const oldRead = deferred<{ approvals: HpFileApproval[] }>();
    const approved = { ...approval, status: "approved" as const };
    const api = {
      decideFileApproval: vi.fn().mockResolvedValue({ approval: approved }),
      listRunFileApprovals: vi
        .fn()
        .mockReturnValueOnce(oldRead.promise)
        .mockResolvedValue({ approvals: [approved] }),
      getRun: vi.fn().mockResolvedValue(work("A")),
    };
    const store = createRunInspector(api as never);
    store.getState().select("A");
    store.setState({ approvals: [approval] });
    const before = store.getState().loadApprovals();
    await store.getState().decide(approval, "approve");
    oldRead.resolve({ approvals: [approval] });
    await before;
    expect(api.listRunFileApprovals.mock.calls[0]?.[1]?.aborted).toBe(true);
    expect(store.getState().approvals[0]?.status).toBe("approved");
    await store.getState().decide(approval, "reject");
    expect(api.decideFileApproval).toHaveBeenCalledTimes(1);
  });
  it("refuses an old approval button after selection changes to another Run", async () => {
    const api = { decideFileApproval: vi.fn() };
    const store = createRunInspector(api as never);
    store.getState().select("B");
    await store.getState().decide(approval, "approve");
    expect(api.decideFileApproval).not.toHaveBeenCalled();
  });
  it("never replays a 409 or an expired approval", async () => {
    const api = {
      decideFileApproval: vi.fn().mockRejectedValue(
        new HpCommandError(409, {
          code: "approval_not_pending",
          message: "conflict",
          request_id: null,
          retryable: false,
          details: {},
        }),
      ),
      listRunFileApprovals: vi.fn().mockResolvedValue({ approvals: [approval] }),
    };
    const store = createRunInspector(api as never);
    store.getState().select("A");
    await store.getState().decide(approval, "approve");
    await store.getState().decide(approval, "approve", true);
    await store
      .getState()
      .decide(
        { ...approval, approval_id: "expired", expires_at: "2000-01-01T00:00:00Z" },
        "approve",
      );
    expect(api.decideFileApproval).toHaveBeenCalledTimes(1);
  });
});
