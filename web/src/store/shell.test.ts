import { beforeEach, describe, expect, it } from "vitest";
import { parseRoute, serializeRoute, useShell } from "./shell";

beforeEach(() => {
  useShell.getState().reset();
  window.history.replaceState(null, "", "#/ai");
});
describe("UI-1 navigation", () => {
  it.each([
    ["#chat", "ai"],
    ["#files", "workspace"],
    ["#works", "tasks"],
    ["#research", "tasks"],
    ["#artifacts", "ai"],
    ["#diagnostics", "ai"],
  ])("recycles %s without a fourth screen", (hash, screen) => {
    expect(parseRoute(hash).route.screen).toBe(screen);
  });
  it("uses only explicit context for legacy object routes", () => {
    expect(parseRoute("#authority").modal).toBeUndefined();
    expect(parseRoute("#authority", { conversationId: "c" }).modal).toBe("resources");
    expect(parseRoute("#account").modal).toBe("account");
    expect(parseRoute("#diagnostics", { runId: "r" }).route.inspector).toEqual({
      kind: "run",
      objectId: "r",
    });
  });
  it.each([
    "#/ai/c1?inspect=artifact%3Aa1&version=v1",
    "#/workspace?dir=d1",
    "#/tasks?bucket=waiting&type=research&work=w1",
    "#/ai/%E0%A4%A?inspect=unknown:secret",
    "#/tasks?bucket=invalid&type=invalid&message=private",
  ])("normalizes and roundtrips %s", (hash) => {
    const route = parseRoute(hash).route;
    const canonical = serializeRoute(route);
    expect(parseRoute(canonical).route).toEqual(route);
    expect(canonical).not.toContain("private");
  });
  it("pushes object history, bounds the internal stack, and clears it on page switch", () => {
    const shell = useShell.getState();
    const length = window.history.length;
    shell.openInspector({ kind: "task", objectId: "parent" });
    expect(window.history.length).toBe(length + 1);
    for (let i = 0; i < 9; i++) shell.openInspector({ kind: "run", objectId: `r${i}` }, true);
    expect(useShell.getState().backStack).toHaveLength(5);
    shell.back();
    expect(useShell.getState().route.inspector?.objectId).toBe("r7");
    shell.navigate({ screen: "workspace" });
    expect(useShell.getState().backStack).toEqual([]);
    expect(useShell.getState().route.inspector).toBeUndefined();
  });
});
