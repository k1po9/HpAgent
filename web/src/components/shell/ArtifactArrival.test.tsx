import { act, fireEvent, render, screen, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InspectorHost } from "./InspectorHost";
import { useShell } from "../../store/shell";
import { useWorkbench } from "../../store/workbench";
import { useArtifacts } from "../../store/artifacts";
import { useArtifactUi } from "../../store/artifactUi";
import { playFilament } from "../spatial/filament";
import { setMotionPreference } from "../motion/preference";
import type { HpArtifact, HpArtifactVersion } from "../../api/types";
vi.mock("../spatial/filament", () => ({ playFilament: vi.fn(() => vi.fn()) }));
const flights = vi.mocked(playFilament);
let source: HTMLButtonElement;
let pending: Map<string, (items: HpArtifactVersion[] | null) => void>;
const artifact = (id: string): HpArtifact => ({
  artifact_id: id,
  title: `成果 ${id}`,
  kind: "html",
  conversation_id: null,
  source_message_id: null,
  created_at: "2026-10-15",
  updated_at: "2026-10-15",
});
const version = (id: string, n: number): HpArtifactVersion => ({
  artifact_version_id: `${id}-v${n}`,
  artifact_id: id,
  version: n,
  parent_version_id: null,
  status: "completed",
  instruction: null,
  html: `<p>${id} v${n}</p>`,
  failure: null,
  created_at: "2026-10-15",
  started_at: null,
  completed_at: "2026-10-15",
});
beforeEach(() => {
  useShell.getState().reset();
  useWorkbench.getState().reset();
  useArtifacts.getState().reset();
  useArtifactUi.getState().reset();
  setMotionPreference("system");
  flights.mockClear();
  pending = new Map();
  source = document.createElement("button");
  source.id = "artifact-source";
  document.body.append(source);
  vi.spyOn(useArtifacts.getState(), "loadArtifact").mockImplementation((id) => {
    useArtifacts.setState((s) => ({ queries: { ...s.queries, [id]: { loading: true } } }));
    return new Promise((done) => pending.set(id, done));
  });
});
afterEach(() => {
  cleanup();
  source.remove();
  vi.restoreAllMocks();
});
const open = (id: string, versionId?: string) =>
  useShell
    .getState()
    .openInspector({ kind: "artifact", objectId: id, versionId, origin: { triggerId: source.id } });
async function loaded(id: string, items = [version(id, 1), version(id, 2)]) {
  await act(async () => {
    useArtifacts.setState((s) => ({
      artifactsById: { ...s.artifactsById, [id]: artifact(id) },
      versionsByArtifactId: { ...s.versionsByArtifactId, [id]: items },
      queries: { ...s.queries, [id]: { loading: false } },
    }));
    pending.get(id)!(items);
  });
}
function setup() {
  render(<InspectorHost compact={false} onSaveHtml={vi.fn()} />);
}
function land() {
  act(() => {
    const f = flights.mock.calls.at(-1)![0];
    f.arrive(true);
    f.settle();
  });
}
it("queries immediately; default version resolution stays in the same gated arrival", async () => {
  setup();
  act(() => open("a"));
  expect(useArtifacts.getState().loadArtifact).toHaveBeenCalledWith("a", false);
  expect(flights).toHaveBeenCalledTimes(1);
  expect(flights.mock.calls[0]![0].target.id).toBe("inspector-artifact-title");
  await loaded("a");
  expect(useShell.getState().route.inspector?.versionId).toBe("a-v2");
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  expect(screen.queryByRole("tab")).toBeNull();
  expect(flights).toHaveBeenCalledTimes(1);
  land();
  expect(screen.getByRole("complementary")).toHaveAttribute("aria-label", "HTML 成果");
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>a v2</p>");
});
it("manual version selection during flight wins over initialization without replay", async () => {
  setup();
  act(() => open("a"));
  act(() => {
    const shell = useShell.getState();
    shell.navigate(
      { ...shell.route, inspector: { ...shell.route.inspector!, versionId: "a-v1" } },
      true,
    );
  });
  await loaded("a");
  expect(useShell.getState().route.inspector?.versionId).toBe("a-v1");
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  expect(flights).toHaveBeenCalledTimes(1);
  land();
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>a v1</p>");
});
it("a local tab change during loading does not discard default normalization", async () => {
  setup();
  act(() => open("a"));
  act(() => useShell.getState().setInspectorTab("versions"));
  await loaded("a");
  expect(useShell.getState().route.inspector).toMatchObject({ versionId: "a-v2", tab: "versions" });
  expect(flights).toHaveBeenCalledTimes(1);
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  land();
  expect(screen.getByRole("tab", { name: "版本历史" })).toHaveAttribute("aria-selected", "true");
});
it("same version iframe and draft survive tabs and expansion; version selection replaces only the preview", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  land();
  const frame = screen.getByTitle("Artifact 预览");
  fireEvent.change(screen.getByLabelText("修改指令"), { target: { value: "保留这份修改草稿" } });
  act(() => useShell.getState().setInspectorTab("versions"));
  expect(screen.getByTitle("Artifact 预览")).toBe(frame);
  expect(frame).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "查看 v1" }));
  expect(useShell.getState().route.inspector?.versionId).toBe("a-v1");
  act(() => useShell.getState().setInspectorTab("preview"));
  const historical = screen.getByTitle("Artifact 预览");
  expect(historical).not.toBe(frame);
  expect(historical).toHaveAttribute("srcdoc", "<p>a v1</p>");
  act(() => useShell.setState({ expanded: true }));
  expect(screen.getByTitle("Artifact 预览")).toBe(historical);
  expect(screen.getByLabelText("修改指令")).toHaveValue("保留这份修改草稿");
  expect(flights).toHaveBeenCalledTimes(1);
});
it("late data and old arrival cannot reveal or normalize a different selected artifact", async () => {
  setup();
  act(() => open("a"));
  const old = flights.mock.calls.at(-1)![0];
  act(() => open("b"));
  await loaded("a");
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(useShell.getState().route.inspector).toMatchObject({
    objectId: "b",
    versionId: undefined,
  });
  expect(document.querySelector("[data-inspector-gate]")).toHaveAttribute("hidden");
  await loaded("b");
  land();
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>b v2</p>");
  expect(screen.queryByText("成果 a")).toBeNull();
});
it("permission loss settles safely before arrival and removes the cached preview", async () => {
  setup();
  act(() => open("a"));
  await loaded("a");
  const old = flights.mock.calls.at(-1)![0];
  act(() =>
    useArtifacts.setState({ queries: { a: { loading: false, error: "对象不可用", status: 403 } } }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("对象不可用。");
  expect(screen.queryByTitle("Artifact 预览")).toBeNull();
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(screen.getByRole("alert")).toHaveTextContent("对象不可用。");
  expect(document.querySelector("[data-inspector-gate]")).not.toHaveAttribute("hidden");
});
it("closing during a pending load never reopens the artifact when data returns", async () => {
  setup();
  act(() => open("a"));
  const old = flights.mock.calls.at(-1)![0];
  act(() => useShell.getState().closeInspector());
  await loaded("a");
  act(() => {
    old.arrive(true);
    old.settle();
  });
  expect(useShell.getState().route.inspector).toBeUndefined();
  expect(screen.queryByTitle("Artifact 预览")).toBeNull();
  expect(document.querySelector(".hp-inspector-exit")?.textContent).toBe("");
});

it("arrival before uncached versions shows current loading until this artifact resolves", async () => {
  setup();
  useArtifacts.setState({ artifactsById: { a: artifact("a") } });
  act(() => open("a"));
  land();
  expect(screen.getByText("正在加载版本内容…")).toHaveAttribute("role", "status");
  expect(screen.queryByTitle("Artifact 预览")).toBeNull();
  await loaded("a");
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>a v2</p>");
});
