import type { HpWork, HpTaskRun } from "../../api/types";
export function workFixture(patch: Partial<HpWork> = {}): HpWork {
  return {
    work_id: "w1",
    title: "测试任务",
    status: "active",
    row_version: 1,
    current_requirement_revision: 2,
    control_epoch: 3,
    active_coordinator_run_id: null,
    conversation_ids: [],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-07T00:00:00Z",
    completed_at: null,
    stopped_at: null,
    schedule: null,
    continuation: { kind: "ready", reason: "accepted" },
    requirement: {
      objective: "目标",
      capability_key: "generic_work",
      spec: { schema_version: 1, reasoning_mode: "react" },
      constraints: [],
      acceptance_criteria: [],
      completion_mode: "deliverable",
      timing: { schema_version: 1, kind: "immediate", timezone: "UTC" },
      resource_requests: [],
      deliverable_policy: { schema_version: 1, required: false },
    },
    budget: {
      limits: { model_total_tokens: 1000, tool_calls: 10 },
      used: { model_total_tokens: 100 },
      reserved: { model_total_tokens: 20 },
      version: 1,
    },
    artifacts: [],
    deliveries: [],
    workspace_saves: [],
    ...patch,
  };
}
export const deliveryFixture: HpWork["deliveries"][number] = {
  delivery_id: "delivery-1",
  notification_id: "notification-1",
  state: "uncertain",
  channel: "qq",
  purpose: "fulfillment",
  requirement_revision: 2,
  last_error: null,
  provider_receipt: null,
};
export const artifactFixture: HpWork["artifacts"][number] = {
  artifact_id: "artifact-1",
  artifact_version_id: "version-1",
  producing_run_id: "run-1",
  role: "deliverable",
  status: "completed",
  accepted_for_revision: null,
  source_requirement_revision: 2,
};
export const runFixture: HpTaskRun = {
  run_id: "run-1",
  work_id: "w1",
  status: "succeeded",
  requirement_revision: 2,
  work_control_epoch: 3,
  result_json: { evidence: [{ type: "artifact_version", ref: "version-1" }] },
  failure_code: null,
};
