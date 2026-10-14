import type { HpRunStatus } from "../../api/types";

export type ComposerActionPhase =
  | "idle"
  | "focused"
  | "ready"
  | "intent"
  | "composing"
  | "blocked"
  | "submitting"
  | "uncertain"
  | "active"
  | "stopping";
/** Presentation only. Actual submit/stop and idempotency remain with their existing owners. */
export function deriveComposerActionPhase(facts: {
  draft: string;
  sendDisabled: boolean;
  attachmentsReady: boolean;
  submitting: boolean;
  uncertain: boolean;
  status: HpRunStatus | null;
  stopping: boolean;
  composing: boolean;
  focused: boolean;
  intent: boolean;
}): ComposerActionPhase {
  if (facts.status === "cancelling" || facts.stopping) return "stopping";
  if (facts.status === "queued" || facts.status === "running") return "active";
  if (facts.submitting) return "submitting";
  if (facts.uncertain) return "uncertain";
  if (facts.sendDisabled || !facts.attachmentsReady) return "blocked";
  if (facts.composing) return "composing";
  if (facts.draft.trim()) return facts.intent ? "intent" : "ready";
  return facts.focused ? "focused" : "idle";
}
