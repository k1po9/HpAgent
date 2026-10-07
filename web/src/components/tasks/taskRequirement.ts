import type { HpRequirement } from "../../api/types";
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
  },
): HpRequirement {
  const sameType = base?.capability_key === values.capability;
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
  return {
    objective: values.objective,
    capability_key: values.capability,
    spec,
    constraints: values.constraints
      .split("\n")
      .map((v) => v.trim())
      .filter(Boolean),
    acceptance_criteria: sameType
      ? base.acceptance_criteria
      : [
          {
            id: "result",
            required: true,
            evidence_types: [
              values.capability === "reminder"
                ? "delivery_receipt"
                : values.capability === "research_report"
                  ? "research_report"
                  : "operation_receipt",
            ],
          },
        ],
    resource_requests: base?.resource_requests ?? [],
    deliverable_policy: base?.deliverable_policy ?? { schema_version: 1, required: false },
    completion_mode:
      base && !values.timingChanged
        ? base.completion_mode
        : values.timing === "daily"
          ? "ongoing"
          : "deliverable",
    timing,
  };
}
