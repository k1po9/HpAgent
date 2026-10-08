import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { ArtifactInspector } from "./ArtifactInspector";
import { useArtifacts } from "../../store/artifacts";
import { useArtifactUi } from "../../store/artifactUi";
import { useShell } from "../../store/shell";
import { useWorks } from "../../store/works";
import type { HpArtifactVersion } from "../../api/types";
const artifact = {
  artifact_id: "a",
  title: "测试/HTML",
  kind: "html" as const,
  conversation_id: null,
  source_message_id: null,
  created_at: "2026-10-08",
  updated_at: "2026-10-08",
};
const v1: HpArtifactVersion = {
  artifact_version_id: "v1",
  artifact_id: "a",
  version: 1,
  parent_version_id: null,
  status: "completed",
  instruction: null,
  html: "<p>v1</p>",
  failure: null,
  created_at: "2026-10-08",
  started_at: null,
  completed_at: "2026-10-08",
};
const v2 = {
  ...v1,
  artifact_version_id: "v2",
  version: 8,
  parent_version_id: "v1",
  instruction: "最新指令",
  html: "<p>v8</p>",
};

beforeEach(() => {
  useArtifacts.getState().reset();
  useArtifactUi.getState().reset();
  useShell.getState().reset();
  useWorks.getState().reset();
});
afterEach(() => {
  vi.restoreAllMocks();
  useArtifacts.getState().reset();
  useShell.getState().reset();
});
function Connected() {
  const inspector = useShell((state) => state.route.inspector);
  return inspector ? <ArtifactInspector key={inspector.objectId} inspector={inspector} /> : null;
}
function pendingLoad(cached = true, explicit?: string) {
  useArtifacts.setState({
    artifactsById: cached ? { a: artifact } : {},
    versionsByArtifactId: cached ? { a: [v1] } : {},
    queries: { a: { loading: false } },
  });
  let finish!: (success?: boolean) => void;
  vi.spyOn(useArtifacts.getState(), "loadArtifact").mockImplementation(() => {
    useArtifacts.setState({ queries: { a: { loading: true } } });
    return new Promise((resolve) => {
      finish = (success = true) => {
        useArtifacts.setState(
          success
            ? {
                artifactsById: { a: artifact },
                versionsByArtifactId: { a: [v1, v2] },
                queries: { a: { loading: false } },
              }
            : { queries: { a: { loading: false, error: "offline" } } },
        );
        resolve(success ? [v1, v2] : null);
      };
    });
  });
  useShell.setState({
    route: { screen: "ai", inspector: { kind: "artifact", objectId: "a", versionId: explicit } },
  });
  const view = render(<Connected />);
  return { view, finish: (success = true) => act(async () => finish(success)) };
}

it("R3: a cold load initializes the numerical latest successful version", async () => {
  const { finish } = pendingLoad(false);
  expect(screen.getByText("正在加载 HTML 成果…")).toBeVisible();
  await finish();
  expect(useShell.getState().route.inspector?.versionId).toBe("v2");
});

it("R3: an explicit historical deep link survives a newer successful load", async () => {
  const { finish } = pendingLoad(true, "v1");
  await finish();
  expect(useShell.getState().route.inspector?.versionId).toBe("v1");
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>v1</p>");
});

it("R3: a manual selection during loading takes precedence over initialization", async () => {
  const { finish } = pendingLoad();
  expect(useShell.getState().route.inspector?.versionId).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "查看 v1" }));
  await finish();
  expect(useShell.getState().route.inspector?.versionId).toBe("v1");
});

it("R3: a network failure keeps cached HTML without fixing the default, then retry initializes fresh data", async () => {
  const { finish } = pendingLoad();
  await finish(false);
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>v1</p>");
  expect(useShell.getState().route.inspector?.versionId).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "重试同步" }));
  await finish();
  expect(useShell.getState().route.inspector?.versionId).toBe("v2");
});

it("R3: a late load after closing does not reopen or select an Inspector", async () => {
  const { finish } = pendingLoad();
  act(() => useShell.getState().closeInspector());
  await finish();
  expect(useShell.getState().route.inspector).toBeUndefined();
  expect(screen.queryByTitle("Artifact 预览")).toBeNull();
});

it("R3: a load from the previous account cannot initialize the new route", async () => {
  const { finish } = pendingLoad();
  act(() => {
    useShell.getState().reset();
    useArtifacts.getState().reset();
  });
  await finish();
  expect(useShell.getState().route.inspector).toBeUndefined();
});

it("R3: A-B-A ignores the first mount's late initialization", async () => {
  const pending: { id: string; finish: () => void }[] = [];
  vi.spyOn(useArtifacts.getState(), "loadArtifact").mockImplementation(
    (id) =>
      new Promise((resolve) => {
        pending.push({
          id,
          finish: () => {
            useArtifacts.setState({
              artifactsById: { [id]: { ...artifact, artifact_id: id } },
              versionsByArtifactId: { [id]: [v1, v2] },
            });
            resolve(id === "a" ? [v1, v2] : []);
          },
        });
      }),
  );
  act(() => useShell.getState().openInspector({ kind: "artifact", objectId: "a" }));
  render(<Connected />);
  act(() => useShell.getState().openInspector({ kind: "artifact", objectId: "b" }));
  act(() => useShell.getState().openInspector({ kind: "artifact", objectId: "a" }));
  expect(pending.map((p) => p.id)).toEqual(["a", "b", "a"]);
  await act(async () => pending[0]!.finish());
  expect(useShell.getState().route.inspector?.versionId).toBeUndefined();
  await act(async () => pending[2]!.finish());
  await waitFor(() => expect(useShell.getState().route.inspector?.versionId).toBe("v2"));
});

it("R3: reopening without a version waits for the fresh latest successful version", async () => {
  useArtifacts.getState().reset();
  useArtifactUi.getState().reset();
  useShell.getState().reset();
  useWorks.getState().reset();
  useArtifacts.setState({
    artifactsById: { a: artifact },
    versionsByArtifactId: { a: [v1] },
    queries: { a: { loading: false } },
  });
  let finish!: () => void;
  const load = vi.spyOn(useArtifacts.getState(), "loadArtifact").mockImplementation(() => {
    useArtifacts.setState({ queries: { a: { loading: true } } });
    return new Promise((resolve) => {
      finish = () => {
        useArtifacts.setState({
          versionsByArtifactId: { a: [v1, v2] },
          queries: { a: { loading: false } },
        });
        resolve([v1, v2]);
      };
    });
  });
  useShell.setState({ route: { screen: "ai", inspector: { kind: "artifact", objectId: "a" } } });
  function Connected() {
    const inspector = useShell((state) => state.route.inspector);
    return <ArtifactInspector inspector={inspector!} />;
  }
  const view = render(<Connected />);
  try {
    await act(async () => finish());
    expect(useShell.getState().route.inspector?.versionId).toBe("v2");
    expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>v8</p>");
  } finally {
    view.unmount();
    load.mockRestore();
    useArtifacts.getState().reset();
    useShell.getState().reset();
  }
});
