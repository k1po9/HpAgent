import { expect, it } from "vitest";
import { deriveComposerActionPhase } from "./policy";
const facts = {
  draft: "下一步",
  sendDisabled: false,
  attachmentsReady: true,
  submitting: false,
  uncertain: false,
  status: null,
  stopping: false,
  composing: false,
  focused: true,
  intent: true,
};
it("only eligible draft creates ready/intent", () => {
  expect(deriveComposerActionPhase({ ...facts, intent: false })).toBe("ready");
  expect(deriveComposerActionPhase(facts)).toBe("intent");
  expect(deriveComposerActionPhase({ ...facts, draft: "  " })).toBe("focused");
});
it.each([
  ["sendDisabled", "blocked"],
  ["attachmentsReady", "blocked"],
  ["submitting", "submitting"],
  ["uncertain", "uncertain"],
  ["composing", "composing"],
])("%s never signals ready", (key, phase) => {
  expect(deriveComposerActionPhase({ ...facts, [key]: key !== "attachmentsReady" })).toBe(phase);
});
it("authoritative execution wins over input and unsettled send", () => {
  expect(deriveComposerActionPhase({ ...facts, status: "queued", submitting: true })).toBe(
    "active",
  );
  expect(deriveComposerActionPhase({ ...facts, status: "running" })).toBe("active");
  expect(deriveComposerActionPhase({ ...facts, status: "cancelling" })).toBe("stopping");
  expect(deriveComposerActionPhase({ ...facts, status: "succeeded", intent: false })).toBe("ready");
});
