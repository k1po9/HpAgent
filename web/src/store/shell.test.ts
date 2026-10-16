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

it("normalizes artifact_build into general and validates tabs for each Inspector kind", () => {
  const parsed = parseRoute("#/tasks?type=artifact_build&work=w1&tab=outputs");
  expect(parsed.route.type).toBe("general");
  expect(parsed.route.inspector).toMatchObject({ kind: "task", objectId: "w1", tab: "outputs" });
  expect(parseRoute("#/tasks?work=w1&tab=preview").route.inspector?.tab).toBeUndefined();
  expect(parseRoute("#/ai?inspect=file:f1&tab=outputs").route.inspector?.tab).toBeUndefined();
  expect(serializeRoute(parsed.route)).toContain("type=general");
  expect(serializeRoute(parsed.route)).toContain("tab=outputs");
});

it("preserves Artifact details and explicit versions through route normalization", () => {
  const parsed = parseRoute(
    "#/tasks?work=task1&inspect=artifact:a1&version=missing&tab=details",
  ).route;
  expect(parsed.inspector).toEqual({
    kind: "artifact",
    objectId: "a1",
    versionId: "missing",
    tab: "details",
  });
  expect(parseRoute(serializeRoute(parsed)).route).toEqual(parsed);
});

it("validates Inspector tabs centrally and replaces the URL without growing history", () => {
  useShell.getState().reset();
  useShell.getState().openInspector({ kind: "run", objectId: "r" });
  const length = window.history.length;
  useShell.getState().setInspectorTab("advanced");
  expect(useShell.getState().route.inspector?.tab).toBe("advanced");
  expect(window.location.hash).toContain("tab=advanced");
  expect(window.history.length).toBe(length);
  useShell.getState().setInspectorTab("versions");
  expect(useShell.getState().route.inspector?.tab).toBe("advanced");
});

it.each(["dirtyTaskEditor", "dirtyResourceEditor"] as const)(
  "does not corrupt child history when %s blocks navigation; confirmation preserves it",
  (guard) => {
    const shell = useShell.getState();
    shell.openInspector({ kind: "run", objectId: "root" });
    shell.openInspector({ kind: "run", objectId: "parent" }, true);
    useShell.setState({ [guard]: "draft" });
    const hash = window.location.hash;
    shell.openInspector({ kind: "run", objectId: "child" }, true);
    expect(useShell.getState().route.inspector?.objectId).toBe("parent");
    expect(useShell.getState().backStack.map((i) => i.objectId)).toEqual(["root"]);
    expect(window.location.hash).toBe(hash);
    const next = useShell.getState().pendingRoute;
    const retry = useShell.getState().pendingResourceChange;
    useShell.setState({ [guard]: null, pendingResourceChange: null });
    if (retry) retry();
    else shell.navigate(next!);
    expect(useShell.getState().backStack.map((i) => i.objectId)).toEqual(["root", "parent"]);
    shell.back();
    expect(useShell.getState().route.inspector?.objectId).toBe("parent");
    shell.closeInspector();
    expect(useShell.getState().route.inspector).toBeUndefined();
    expect(useShell.getState().backStack).toEqual([]);
  },
);
it("guarded back confirmation restores the remaining stack rather than losing ancestors", () => {
  const shell = useShell.getState();
  for (const id of ["root", "parent", "child"])
    shell.openInspector({ kind: "run", objectId: id }, true);
  useShell.setState({ dirtyTaskEditor: "draft" });
  shell.back();
  expect(useShell.getState().route.inspector?.objectId).toBe("child");
  expect(useShell.getState().backStack.map((i) => i.objectId)).toEqual(["root", "parent"]);
  const next = useShell.getState().pendingRoute!;
  useShell.setState({ dirtyTaskEditor: null });
  shell.navigate(next);
  expect(useShell.getState().route.inspector?.objectId).toBe("parent");
  expect(useShell.getState().backStack.map((i) => i.objectId)).toEqual(["root"]);
});
it.each(["close", "tab", "back"] as const)(
  "dirty guard cancels %s without changing object, URL, token or history",
  (operation) => {
    const shell = useShell.getState();
    shell.openInspector({ kind: "run", objectId: "A" });
    shell.openInspector({ kind: "run", objectId: "B" }, true);
    const before = useShell.getState(),
      hash = window.location.hash;
    useShell.setState({ dirtyTaskEditor: "draft" });
    if (operation === "close") shell.closeInspector();
    else if (operation === "tab") shell.setInspectorTab("advanced");
    else shell.back();
    expect(useShell.getState().route).toBe(before.route);
    expect(window.location.hash).toBe(hash);
    expect(useShell.getState().backStack).toEqual(before.backStack);
    expect(useShell.getState().requestToken).toBe(before.requestToken);
    useShell.setState({ pendingRoute: null, dirtyTaskEditor: null });
    expect(useShell.getState().route.inspector?.objectId).toBe("B");
  },
);

it("keeps the guarded history destination when its paired hashchange sees the restored current URL", () => {
  const current = {
    screen: "tasks" as const,
    inspector: { kind: "task" as const, objectId: "w1", tab: "overview" as const },
  };
  useShell.getState().navigate(current);
  useShell.setState({ dirtyTaskEditor: "w1" });
  const desired = { ...current, inspector: { ...current.inspector, tab: "advanced" as const } };
  useShell.getState().restore(serializeRoute(desired));
  const pending = useShell.getState().pendingRoute;
  expect(pending?.inspector?.tab).toBe("advanced");
  useShell.getState().restore(window.location.hash);
  expect(useShell.getState().pendingRoute).toBe(pending);
  useShell.setState({ dirtyTaskEditor: null });
  useShell.getState().navigate(pending!);
  expect(useShell.getState().route.inspector?.tab).toBe("advanced");
});
