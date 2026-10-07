import { expect, it } from "vitest";
import { buildRequirement, localToInstant, localValue } from "./taskRequirement";
import { workFixture } from "./taskFixtures";
it("preserves full research policy, source strategy, timezone and ongoing mode", () => {
  const base = {
    ...workFixture().requirement,
    capability_key: "research_report",
    completion_mode: "ongoing" as const,
    spec: { schema_version: 1, source_strategy: { languages: ["zh"] }, report_format: "long" },
    resource_requests: [{ node_id: "node", operations: ["read_content"] }],
    deliverable_policy: { schema_version: 1, required: true, directory_id: "folder" },
    timing: {
      schema_version: 1,
      kind: "once",
      timezone: "America/New_York",
      due_at: "2027-01-01T15:00:00Z",
    },
  };
  const result = buildRequirement(base, {
    objective: "updated",
    capability: "research_report",
    constraints: "new constraint",
    mode: "react",
    timing: "once",
    zone: "America/New_York",
    due: "",
    time: "09:00",
    timingChanged: false,
  });
  expect(result.spec).toEqual(base.spec);
  expect(result.acceptance_criteria).toEqual(base.acceptance_criteria);
  expect(result.resource_requests).toEqual(base.resource_requests);
  expect(result.deliverable_policy).toEqual(base.deliverable_policy);
  expect(result.timing).toEqual(base.timing);
  expect(result.completion_mode).toBe("ongoing");
  expect(result.objective).toBe("updated");
});
it("converts explicit input timezone independently of browser TZ and retains reminder target", () => {
  expect(localToInstant("2027-01-01T09:00", "Asia/Shanghai")).toBe("2027-01-01T01:00:00.000Z");
  expect(localToInstant("2027-01-01T09:00", "UTC")).toBe("2027-01-01T09:00:00.000Z");
  expect(localValue("2027-01-01T01:00:00Z", "Asia/Shanghai")).toBe("2027-01-01T09:00");
  expect(() => localToInstant("2027-01-01T09:00", "America/New_York")).toThrow("保留原值");
  const base = {
    ...workFixture().requirement,
    capability_key: "reminder",
    spec: { schema_version: 1, content: "old", target_ref: "authorized-target" },
  };
  expect(
    buildRequirement(base, {
      objective: "new",
      capability: "reminder",
      constraints: "",
      mode: "react",
      timing: "immediate",
      zone: "UTC",
      due: "",
      time: "09:00",
      timingChanged: false,
    }).spec,
  ).toEqual({ ...base.spec, content: "new" });
});

it("omits persisted Requirement row metadata from a revision command", () => {
  const base = {
    ...workFixture().requirement,
    account_id: "private-account",
    work_id: "work",
    revision: 2,
    created_at: "today",
    change_reason: "old",
    command_id: "prior-command",
    content_hash: "hash",
  };
  const value = buildRequirement(base, {
    objective: "updated",
    capability: "generic_work",
    constraints: "",
    mode: "react",
    timing: "immediate",
    zone: "UTC",
    due: "",
    time: "09:00",
    timingChanged: false,
  });
  expect(Object.keys(value).sort()).toEqual(
    [
      "acceptance_criteria",
      "capability_key",
      "completion_mode",
      "constraints",
      "deliverable_policy",
      "objective",
      "resource_requests",
      "spec",
      "timing",
    ].sort(),
  );
});
