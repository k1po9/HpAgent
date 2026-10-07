import { expect, it } from "vitest";
import type { HpWork } from "../../api/types";
import { presentTask, compareTasks, taskType, taskTime } from "./taskPresentation";
import { acceptanceEligible, canRetryDelivery, taskActions } from "./taskActions";
import {
  workFixture,
  deliveryFixture as d,
  artifactFixture as a,
  runFixture as run,
} from "./taskFixtures";
const now = Date.parse("2026-10-07T12:00:00Z");
const c = (kind: string, reason = "accepted", extra: Record<string, string> = {}) => ({
  kind,
  reason,
  ...extra,
});
const vectors: Array<[string, Partial<HpWork>, string, string]> = [
  [
    "M01",
    { status: "completed", deliveries: [{ ...d, requirement_revision: 1 }] },
    "ended",
    "已完成",
  ],
  ["M02", { status: "stopped" }, "ended", "已停止"],
  ["M03", { status: "pausing", deliveries: [d] }, "active", "正在暂停"],
  [
    "M04",
    {
      status: "stopping",
      continuation: c("blocked", "side_effect_uncertain", { operation_ref: "old-operation" }),
    },
    "active",
    "正在停止",
  ],
  ["M05", { deliveries: [d], active_coordinator_run_id: "run" }, "attention", "发送结果待确认"],
  ["M06", { deliveries: [{ ...d, requirement_revision: 1 }] }, "waiting", "准备继续"],
  [
    "M07",
    { continuation: c("awaiting_input", "user_acceptance_required") },
    "attention",
    "有成果等待确认",
  ],
  ["M08", { continuation: c("blocked", "budget_exhausted") }, "attention", "额度已用尽"],
  ["M09", { continuation: c("blocked", "attempt_failed") }, "attention", "执行受阻，需要处理"],
  [
    "M10",
    { continuation: c("retry_after", "attempt_failed", { due_at: "2026-10-08T12:00:00Z" }) },
    "waiting",
    "等待自动重试",
  ],
  ["M11", { continuation: c("blocked", "future_reason") }, "attention", "执行受阻，需要处理"],
  ["M12", { active_coordinator_run_id: "run" }, "active", "正在执行"],
  [
    "M13",
    { status: "paused", continuation: c("blocked", "delivery_resolved") },
    "waiting",
    "已暂停",
  ],
  ["M14", { status: "paused", deliveries: [d] }, "attention", "发送结果待确认"],
  ["M15", { continuation: c("ready", "waiting_capacity") }, "waiting", "等待执行资源"],
  [
    "M16",
    { continuation: c("at_time", "scheduled", { due_at: "2026-10-01T12:00:00Z" }) },
    "waiting",
    "计划时间已到，等待执行",
  ],
  [
    "M17",
    { continuation: c("awaiting_delivery"), deliveries: [{ ...d, state: "sending" }] },
    "waiting",
    "等待发送或渠道回执",
  ],
  ["M18", { continuation: c("none") }, "waiting", "等待继续"],
  [
    "M19",
    {
      requirement: {
        ...workFixture().requirement,
        completion_mode: "ongoing",
        timing: { schema_version: 1, kind: "daily", timezone: "UTC", local_time: "09:00" },
      },
    },
    "waiting",
    "准备继续",
  ],
  [
    "M20",
    {
      deliveries: [d],
      artifacts: [a],
      budget: { ...workFixture().budget!, used: { model_total_tokens: 1000 } },
      requirement: {
        ...workFixture().requirement,
        acceptance_criteria: [
          { id: "accept", required: true, evidence_types: ["user_acceptance"] },
        ],
      },
    },
    "attention",
    "发送结果待确认",
  ],
  [
    "M21",
    { continuation: undefined as unknown as HpWork["continuation"] },
    "attention",
    "状态待核实",
  ],
];
it.each(vectors)(
  "%s maps Work state once and preserves safe actions",
  (id, patch, bucket, label) => {
    const work = workFixture(patch),
      p = presentTask(work, now);
    expect(p.bucket).toBe(bucket);
    expect(p.label).toBe(label);
    if (["M01", "M02"].includes(id)) expect(taskActions(work, now)).not.toContain("advance");
    if (id === "M03") expect(p.evidenceRefs).toEqual([d.delivery_id]);
    if (id === "M04") expect(p.attentionReasons).toContain("外部操作结果待确认");
    if (id === "M10") expect(taskActions(work, now)).not.toContain("advance");
    if (id === "M20")
      expect(p.attentionReasons).toEqual(["发送结果待确认", "有成果等待确认", "额度已用尽"]);
    if (id === "M21") expect(p.secondaryActions).toEqual([]);
  },
);
it("covers delivery failure, input, future schedule and unknown compatible kinds", () => {
  expect(presentTask(workFixture({ deliveries: [{ ...d, state: "failed" }] }), now).label).toBe(
    "发送失败",
  );
  expect(presentTask(workFixture({ continuation: c("awaiting_input") }), now).label).toBe(
    "等待你的补充",
  );
  const future = workFixture({
    continuation: c("at_time", "scheduled", { due_at: "2027-01-01T00:00:00Z" }),
  });
  expect(presentTask(future, now).label).toBe("已计划");
  expect(taskActions(future, now)).not.toContain("advance");
  expect(presentTask(workFixture({ continuation: c("future_kind") }), now).bucket).toBe("waiting");
  expect(taskActions(workFixture({ continuation: c("future_kind") }), now)).not.toContain(
    "advance",
  );
});
it("handles corrupt times and terminal histories without changing terminal status", () => {
  for (const due_at of ["broken", "2026-10-07T09:00:00", ""]) {
    const w = workFixture({ continuation: c("at_time", "scheduled", { due_at }) });
    expect(presentTask(w, now).label).toBe("状态待核实");
    expect(taskActions(w, now)).toEqual(["view"]);
  }
  expect(
    presentTask(
      workFixture({
        status: "completed",
        continuation: undefined as unknown as HpWork["continuation"],
      }),
      now,
    ).bucket,
  ).toBe("ended");
  expect(presentTask(workFixture({ status: "new_status" as HpWork["status"] }), now).label).toBe(
    "状态待核实",
  );
});
it("sorts waiting times, acceptance before budgets, stable IDs and type aliases", () => {
  const late = workFixture({
    work_id: "b",
    continuation: c("at_time", "scheduled", { due_at: "2027-02-01T00:00:00Z" }),
  });
  const early = workFixture({
    work_id: "a",
    continuation: c("at_time", "scheduled", { due_at: "2027-01-01T00:00:00Z" }),
  });
  expect(compareTasks(early, late, now)).toBeLessThan(0);
  expect(
    compareTasks(workFixture({ work_id: "a" }), workFixture({ work_id: "b" }), now),
  ).toBeLessThan(0);
  expect(
    taskType(
      workFixture({ requirement: { ...early.requirement, capability_key: "artifact_build" } }),
    ),
  ).toBe("general");
  expect(
    taskType(workFixture({ requirement: { ...early.requirement, capability_key: "unknown" } })),
  ).toBe("general");
  expect(
    taskTime(
      workFixture({
        status: "paused",
        schedule: { desired_enabled: true, next_due_at: "2027-01-01T00:00:00Z" },
      }),
    ),
  ).toBe("");
});
it("requires version, revision, epoch, role, successful Run and evidence before acceptance", () => {
  const w = workFixture({
    requirement: {
      ...workFixture().requirement,
      acceptance_criteria: [{ id: "accept", required: true, evidence_types: ["user_acceptance"] }],
    },
  });
  expect(acceptanceEligible(w, a, run)).toBe(true);
  for (const candidate of [
    { ...a, role: "input" as const },
    { ...a, source_requirement_revision: 1 },
    { ...a, accepted_for_revision: 2 },
    { ...a, artifact_version_id: "manually-created-v2" },
  ])
    expect(acceptanceEligible(w, candidate, run)).toBe(false);
  for (const r of [
    { ...run, status: "failed" },
    { ...run, work_control_epoch: 2 },
    { ...run, requirement_revision: 1 },
    { ...run, result_json: {} },
  ])
    expect(acceptanceEligible(w, a, r)).toBe(false);
  expect(acceptanceEligible(w, a)).toBe(false);
});
it("separates fact retry from current fulfillment and prevents terminal resend", () => {
  const w = workFixture({
    continuation: c("awaiting_delivery", "delivery_uncertain", { receipt_ref: d.notification_id! }),
  });
  expect(canRetryDelivery(w, d)).toBe(true);
  expect(canRetryDelivery({ ...w, status: "paused" }, d)).toBe(false);
  expect(canRetryDelivery(w, { ...d, requirement_revision: 1 })).toBe(false);
  expect(
    canRetryDelivery(
      { ...w, status: "paused" },
      { ...d, purpose: "fact", requirement_revision: 1 },
    ),
  ).toBe(true);
  expect(canRetryDelivery({ ...w, status: "completed" }, { ...d, purpose: "fact" })).toBe(false);
});

it("keeps classification tied to continuation while preserving extra facts as attention hints", () => {
  const work = workFixture({
    active_coordinator_run_id: "run",
    budget: { ...workFixture().budget!, used: { model_total_tokens: 1000 } },
  });
  expect(presentTask(work, now).bucket).toBe("active");
  expect(presentTask(work, now).attentionReasons).toContain("额度已用尽");
  const resolved = {
    ...work,
    status: "paused" as const,
    active_coordinator_run_id: null,
    continuation: { kind: "blocked", reason: "delivery_resolved" },
  };
  expect(presentTask(resolved, now).label).toBe("已暂停");
});
