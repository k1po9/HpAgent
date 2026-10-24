import type { HpRequirement } from "../../api/types";
export function hasLegacyGenericCriteria(requirement?: HpRequirement) {
  return (
    requirement?.capability_key === "generic_work" &&
    requirement.acceptance_criteria.some((c) => c.evidence_types.includes("operation_receipt"))
  );
}
export function localToInstant(value: string, zone: string) {
  if (!["UTC", "Asia/Shanghai"].includes(zone)) throw new Error("此时区的计划暂只支持保留原值。");
  const date = new Date(`${value}:00${zone === "UTC" ? "Z" : "+08:00"}`);
  if (!Number.isFinite(date.getTime())) throw new Error("请输入合法日期时间。");
  return date.toISOString();
}
export function localValue(value?: string, zone = "UTC") {
  if (!value || !Number.isFinite(Date.parse(value))) return "";
  try {
    const parts = new Intl.DateTimeFormat("sv-SE", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(value));
    return parts.replace(" ", "T");
  } catch {
    return "";
  }
}
export function buildRequirement(
  base: HpRequirement | undefined,
  values: {
    objective: string;
    capability: string;
    constraints: string;
    mode: string;
    timing: string;
    zone: string;
    due: string;
    time: string;
    timingChanged: boolean;
    saveDirectory?: string;
    saveChanged?: boolean;
    upgradeLegacyCriteria?: boolean;
    formats?: string;
    formatsChanged?: boolean;
  },
): HpRequirement {
  const sameType = base?.capability_key === values.capability;
  if (sameType && hasLegacyGenericCriteria(base) && !values.upgradeLegacyCriteria)
    throw new Error("请先确认将旧操作回执条件升级为已发布成果条件。");
  const spec: Record<string, unknown> = sameType
    ? { ...base.spec }
    : values.capability === "reminder"
      ? { schema_version: 1, target_ref: "account_inbox" }
      : values.capability === "research_report"
        ? { schema_version: 1, source_strategy: {} }
        : { schema_version: 1 };
  if (
    values.capability === "reminder" &&
    (!base || !sameType || values.objective !== base.objective)
  )
    spec.content = values.objective;
  if (values.capability === "generic_work") spec.reasoning_mode = values.mode;
  const timing =
    base && !values.timingChanged
      ? { ...base.timing }
      : {
          schema_version: 1,
          kind: values.timing,
          timezone: values.zone,
          ...(values.timing === "once"
            ? { due_at: localToInstant(values.due, values.zone) }
            : values.timing === "daily"
              ? { local_time: values.time }
              : {}),
        };
  const policy =
    values.saveChanged && values.saveDirectory
      ? {
          schema_version: 1,
          required: Boolean(values.saveDirectory),
          ...(values.saveDirectory ? { directory_id: values.saveDirectory } : {}),
        }
      : (base?.deliverable_policy ?? { schema_version: 1, required: false });
  const criteria = sameType
    ? base.acceptance_criteria.map((criterion) =>
        values.capability === "generic_work" && values.upgradeLegacyCriteria
          ? {
              ...criterion,
              evidence_types: Array.from(
                new Set(
                  criterion.evidence_types.map((type) =>
                    type === "operation_receipt" ? "output_exists" : type,
                  ),
                ),
              ),
            }
          : criterion,
      )
    : [
        {
          id: "result",
          required: true,
          evidence_types: [
            values.capability === "reminder"
              ? "delivery_receipt"
              : values.capability === "research_report"
                ? "research_report"
                : "output_exists",
          ],
        },
      ];
  // Saving and output checks remain independent requirements. Never replace an
  // existing criterion or erase it when changing the destination.
  const acceptance =
    values.saveChanged &&
    values.saveDirectory &&
    values.capability === "generic_work" &&
    !criteria.some((c) => c.required && c.evidence_types.includes("workspace_saved"))
      ? [
          ...criteria,
          {
            id: Array.from(
              { length: criteria.length + 1 },
              (_, i) => `workspace_save_${i + 1}`,
            ).find((id) => !criteria.some((c) => c.id === id))!,
            required: true,
            evidence_types: ["workspace_saved"],
          },
        ]
      : criteria;
  let deliverables = sameType ? base?.deliverables : undefined;
  let finalAcceptance = acceptance;
  if (values.capability === "generic_work" && values.formatsChanged) {
    const owned = new Set(
      base?.deliverables?.items.flatMap((d) => [d.output_criterion_id, d.format_criterion_id]) ??
        [],
    );
    finalAcceptance = acceptance.filter((c) => !owned.has(c.id));
    const formats = (values.formats ?? "").split(",").filter(Boolean) as Array<"markdown" | "docx">;
    if (formats.some((f) => !["markdown", "docx"].includes(f)))
      throw new Error("不支持的交付格式。");
    const reusable = finalAcceptance.filter(
      (c) => c.required && c.evidence_types.length === 1 && c.evidence_types[0] === "output_exists",
    );
    const items = formats.map((format, index) => {
      const prefix = Array.from(
        { length: finalAcceptance.length + 2 },
        (_, i) => `delivery_${format}_${i + 1}`,
      ).find(
        (id) => !finalAcceptance.some((c) => c.id === `${id}.output` || c.id === `${id}.format`),
      )!;
      const outputId = reusable.length === 1 && index === 0 ? reusable[0]!.id : `${prefix}.output`;
      if (!finalAcceptance.some((c) => c.id === outputId))
        finalAcceptance.push({ id: outputId, required: true, evidence_types: ["output_exists"] });
      finalAcceptance.push({
        id: `${prefix}.format`,
        required: true,
        evidence_types: ["check_passed"],
      });
      return {
        id: prefix,
        format,
        required: true,
        output_criterion_id: outputId,
        format_criterion_id: `${prefix}.format`,
      };
    });
    deliverables = items.length ? { schema_version: 1, items } : undefined;
    if (
      !items.length &&
      !finalAcceptance.some((c) => c.required && c.evidence_types.includes("output_exists"))
    ) {
      const id = Array.from(
        { length: finalAcceptance.length + 1 },
        (_, i) => `result_${i + 1}`,
      ).find((id) => !finalAcceptance.some((c) => c.id === id))!;
      finalAcceptance.push({ id, required: true, evidence_types: ["output_exists"] });
    }
  }
  return {
    objective: values.objective,
    capability_key: values.capability,
    spec,
    constraints: values.constraints
      .split("\n")
      .map((v) => v.trim())
      .filter(Boolean),
    acceptance_criteria: finalAcceptance,
    ...(deliverables ? { deliverables } : {}),
    resource_requests: base?.resource_requests ?? [],
    deliverable_policy: policy,
    completion_mode:
      base && !values.timingChanged
        ? base.completion_mode
        : values.timing === "daily"
          ? "ongoing"
          : "deliverable",
    timing,
  };
}
