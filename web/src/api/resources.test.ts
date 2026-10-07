import { expect, it, vi } from "vitest";
import { HpApi } from "./resources";
it("uses existing Run/approval/model-input endpoints and a frozen decision key", async () => {
  const request = vi.fn().mockResolvedValue({ approvals: [], approval: {}, items: [] });
  const api = new HpApi({ request } as never);
  await api.listRunFileApprovals("run");
  await api.decideFileApproval("approval", "approve", "same-intent");
  await api.decideFileApproval("approval", "reject", "other-intent");
  await api.listRunModelInputs("run");
  expect(request.mock.calls.map((c) => c[0])).toEqual([
    { method: "GET", path: "/api/v1/runs/run/file-action-approvals" },
    {
      method: "POST",
      path: "/api/v1/file-action-approvals/approval/approve",
      body: {},
      idempotencyKey: "same-intent",
    },
    {
      method: "POST",
      path: "/api/v1/file-action-approvals/approval/reject",
      body: {},
      idempotencyKey: "other-intent",
    },
    { method: "GET", path: "/api/v1/runs/run/model-inputs" },
  ]);
});
