import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { ReadingPresentation } from "../mercury/readingPresentation";
import { ArtifactMessageItems } from "./ArtifactMessageItems";
import { useArtifacts } from "../../store/artifacts";
import { useShell } from "../../store/shell";
const artifact = {
  artifact_id: "a",
  conversation_id: "c",
  source_message_id: "m",
  kind: "html" as const,
  title: "第一个",
  created_at: "now",
  updated_at: "now",
};
beforeEach(() => {
  useArtifacts.getState().reset();
  useShell.getState().reset();
  vi.spyOn(useArtifacts.getState(), "loadForMessage").mockReset().mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("lists every existing HTML object and opens the chosen object with message origin", () => {
  useArtifacts.setState({
    artifactsByMessageId: {
      m: [
        { artifact, latest_version: null },
        { artifact: { ...artifact, artifact_id: "b", title: "第二个" }, latest_version: null },
      ],
    },
  });
  render(<ArtifactMessageItems messageId="m" />);
  fireEvent.click(screen.getByRole("button", { name: "第一个 · HTML · 暂无版本" }));
  expect(useShell.getState().route.inspector).toMatchObject({
    objectId: "a",
    origin: { conversationId: "c", messageId: "m" },
  });
  expect(screen.getByRole("button", { name: "第二个 · HTML · 暂无版本" })).toBeVisible();
});
it("keeps failed queries explicit and never creates implicitly", () => {
  const create = vi.spyOn(useArtifacts.getState(), "createArtifact");
  useArtifacts.setState({ messageQueries: { m: { loading: false, error: "query failed" } } });
  render(<ArtifactMessageItems messageId="m" />);
  expect(screen.queryByRole("button", { name: "生成 HTML" })).toBeNull();
  expect(screen.getByRole("button", { name: "重试成果查询" })).toBeVisible();
  expect(create).not.toHaveBeenCalled();
});
it("does not navigate after a creation resolves in a different page", async () => {
  useArtifacts.setState({ artifactsByMessageId: { m: [] } });
  let resolve!: (result: { status: "success"; artifact: typeof artifact }) => void;
  vi.spyOn(useArtifacts.getState(), "createArtifact").mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  render(<ArtifactMessageItems messageId="m" />);
  fireEvent.click(screen.getByRole("button", { name: "生成 HTML" }));
  useShell.getState().navigate({ screen: "workspace" });
  resolve({ status: "success", artifact });
  await waitFor(() => expect(useShell.getState().route.screen).toBe("workspace"));
  expect(useShell.getState().route.inspector).toBeUndefined();
});

it("does not reopen an inspector after its creating message has unmounted", async () => {
  useArtifacts.setState({ artifactsByMessageId: { m: [] } });
  let resolve!: (result: { status: "success"; artifact: typeof artifact }) => void;
  vi.spyOn(useArtifacts.getState(), "createArtifact").mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const view = render(<ArtifactMessageItems messageId="m" />);
  fireEvent.click(screen.getByRole("button", { name: "生成 HTML" }));
  view.unmount();
  resolve({ status: "success", artifact });
  await waitFor(() => expect(useShell.getState().route.inspector).toBeUndefined());
});

it("suspends retiring message discovery and loads only the real arriving message", async () => {
  const load = useArtifacts.getState().loadForMessage;
  vi.mocked(load).mockClear();
  const view = render(
    <ReadingPresentation.Provider value={{ retiring: true, run: null }}>
      <ArtifactMessageItems messageId="old-message" />
    </ReadingPresentation.Provider>,
  );
  expect(load).not.toHaveBeenCalled();
  view.rerender(
    <ReadingPresentation.Provider value={{ retiring: false, run: null }}>
      <ArtifactMessageItems messageId="new-message" />
    </ReadingPresentation.Provider>,
  );
  await waitFor(() => expect(load).toHaveBeenCalledExactlyOnceWith("new-message"));
});

it("discovers the latest message after inert releases even when intersection never fires", async () => {
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const load = useArtifacts.getState().loadForMessage;
  const view = render(
    <div inert>
      <ArtifactMessageItems messageId="latest" discoverOnEntry />
    </div>,
  );
  expect(load).not.toHaveBeenCalled();
  view.container.firstElementChild!.removeAttribute("inert");
  await waitFor(() => expect(load).toHaveBeenCalledExactlyOnceWith("latest"));
  view.container.firstElementChild!.setAttribute("inert", "");
  view.container.firstElementChild!.removeAttribute("inert");
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  view.unmount();
  vi.unstubAllGlobals();
});

it("leaves offscreen history undiscovered rather than loading every mounted message", () => {
  const view = render(
    <div className="hp-thread__viewport">
      <ArtifactMessageItems messageId="old" />
    </div>,
  );
  expect(useArtifacts.getState().loadForMessage).not.toHaveBeenCalled();
  view.unmount();
});
