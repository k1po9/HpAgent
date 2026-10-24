import { expect, it } from "vitest";
import { buildRequirement, localToInstant, localValue } from "./taskRequirement";
import { workFixture } from "./taskFixtures";
import contract from "../../../../test/fixtures/work/generic_completion.json";

const genericValues = {
  objective: contract.new_requirement.objective,
  capability: "generic_work",
  constraints: "",
  mode: "react",
  timing: "immediate",
  zone: "UTC",
  due: "",
  time: "09:00",
  timingChanged: false,
};

it("creates the same Generic completion contract accepted by the backend", () => {
  const requirement = buildRequirement(undefined, genericValues);
  expect(requirement).toEqual(contract.new_requirement);
  for (const criterion of requirement.acceptance_criteria)
    for (const type of criterion.evidence_types)
      expect(contract.result_evidence_types).toContain(type);
  for (const mode of ["react", "plan_and_execute"])
    expect(buildRequirement(undefined, { ...genericValues, mode }).acceptance_criteria).toEqual(
      requirement.acceptance_criteria,
    );
});

it("requires explicit upgrade and preserves legacy criterion identity, checks and required flags", () => {
  const legacy = {
    ...workFixture().requirement,
    acceptance_criteria: structuredClone(contract.legacy_criteria),
  };
  const original = structuredClone(legacy);
  expect(() => buildRequirement(legacy, genericValues)).toThrow("请先确认");
  const upgraded = buildRequirement(legacy, { ...genericValues, upgradeLegacyCriteria: true });
  expect(upgraded.acceptance_criteria).toEqual(contract.upgraded_criteria);
  expect(legacy).toEqual(original);
  expect(upgraded.deliverable_policy).toEqual(legacy.deliverable_policy);
  expect(upgraded.resource_requests).toEqual(legacy.resource_requests);
});
it("selects a save directory without granting access and keeps independent checks", () => {
  const base = {
    ...workFixture().requirement,
    capability_key: "generic_work",
    acceptance_criteria: [
      { id: "workspace_save_1", required: true, evidence_types: ["check_passed"] },
    ],
  };
  const result = buildRequirement(base, {
    objective: "make a page",
    capability: "generic_work",
    constraints: "",
    mode: "react",
    timing: "immediate",
    zone: "UTC",
    due: "",
    time: "",
    timingChanged: false,
    saveChanged: true,
    saveDirectory: "chosen-directory",
  });
  expect(result.deliverable_policy).toEqual({
    schema_version: 1,
    required: true,
    directory_id: "chosen-directory",
  });
  expect(result.acceptance_criteria).toEqual([
    ...base.acceptance_criteria,
    { id: "workspace_save_2", required: true, evidence_types: ["workspace_saved"] },
  ]);
  expect(result.resource_requests).toEqual(base.resource_requests);
  const unchanged = buildRequirement(result, {
    objective: "same page",
    capability: "generic_work",
    constraints: "",
    mode: "react",
    timing: "immediate",
    zone: "UTC",
    due: "",
    time: "",
    timingChanged: false,
    saveChanged: true,
    saveDirectory: "",
  });
  expect(unchanged.deliverable_policy).toEqual(result.deliverable_policy);
  expect(unchanged.acceptance_criteria).toEqual(result.acceptance_criteria);
});
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

it("binds each requested format to independent existence and format criteria", () => {
  const result = buildRequirement(undefined, {
    ...genericValues,
    formatsChanged: true,
    formats: "markdown,docx",
  });
  expect(result.deliverables?.items.map((i) => i.format)).toEqual(["markdown", "docx"]);
  const refs = result.deliverables!.items.flatMap((i) => [
    i.output_criterion_id,
    i.format_criterion_id,
  ]);
  expect(new Set(refs).size).toBe(4);
  for (const item of result.deliverables!.items) {
    expect(
      result.acceptance_criteria.find((c) => c.id === item.output_criterion_id)?.evidence_types,
    ).toEqual(["output_exists"]);
    expect(
      result.acceptance_criteria.find((c) => c.id === item.format_criterion_id)?.evidence_types,
    ).toEqual(["check_passed"]);
  }
  expect(result.deliverable_policy.required).toBe(false);
});

it("preserves targets during ordinary revision and replaces only owned criteria when format changes", () => {
  const base = buildRequirement(undefined, {
    ...genericValues,
    formatsChanged: true,
    formats: "markdown",
  });
  base.acceptance_criteria.push({
    id: "quality",
    required: true,
    evidence_types: ["check_passed"],
  });
  const unchanged = buildRequirement(base, { ...genericValues, objective: "revised" });
  expect(unchanged.deliverables).toEqual(base.deliverables);
  const revised = buildRequirement(base, {
    ...genericValues,
    formatsChanged: true,
    formats: "docx",
  });
  expect(revised.deliverables?.items.map((i) => i.format)).toEqual(["docx"]);
  expect(revised.acceptance_criteria).toContainEqual({
    id: "quality",
    required: true,
    evidence_types: ["check_passed"],
  });
  expect(
    revised.acceptance_criteria.some(
      (c) => c.id === base.deliverables!.items[0]!.format_criterion_id,
    ),
  ).toBe(false);
  const cleared = buildRequirement(base, { ...genericValues, formatsChanged: true, formats: "" });
  expect(cleared.deliverables).toBeUndefined();
  expect(
    cleared.acceptance_criteria.some(
      (c) => c.required && c.evidence_types.includes("output_exists"),
    ),
  ).toBe(true);
  expect(base.deliverables?.items[0]!.format).toBe("markdown");
});
