import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { ArtifactInspector } from "./ArtifactInspector";
import { useArtifacts } from "../../store/artifacts";
import { useArtifactUi } from "../../store/artifactUi";
import { useShell } from "../../store/shell";
import { useWorks } from "../../store/works";
import { workFixture, artifactFixture } from "../tasks/taskFixtures";
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
  useArtifacts.setState({ artifactsById: { a: artifact }, versionsByArtifactId: { a: [v2, v1] } });
  vi.spyOn(useArtifacts.getState(), "loadArtifact").mockResolvedValue([v1, v2]);
});
afterEach(() => vi.restoreAllMocks());
function setup(versionId = "v1", tab = "preview" as "preview" | "versions" | "details") {
  useShell.setState({
    route: { screen: "ai", inspector: { kind: "artifact", objectId: "a", versionId, tab } },
  });
  return render(<ArtifactInspector inspector={useShell.getState().route.inspector!} />);
}
it("does not substitute an explicit unavailable version and shows actual version numbers", () => {
  const view = setup("missing");
  expect(screen.queryByTitle("Artifact 预览")).toBeNull();
  expect(screen.getByText(/指定版本不可用/)).toBeVisible();
  view.rerender(
    <ArtifactInspector
      inspector={{ kind: "artifact", objectId: "a", versionId: "v1", tab: "versions" }}
    />,
  );
  expect(screen.getAllByRole("button", { name: "查看 v8" })).toHaveLength(2);
  expect(screen.getByText("基准：v1")).toBeVisible();
});
it("keeps historical selection while presenting the latest successful modification base", () => {
  setup();
  expect(screen.getByTitle("Artifact 预览")).toHaveAttribute("srcdoc", "<p>v1</p>");
  expect(screen.getByText(/基于最近成功版本 v8/)).toBeVisible();
  expect(useShell.getState().route.inspector?.versionId).toBe("v1");
});
it("preserves drafts on failure and protects edits made during a submission", async () => {
  setup();
  const input = screen.getByLabelText("修改指令");
  fireEvent.change(input, { target: { value: "原指令" } });
  let resolve!: (v: { status: "success" }) => void;
  vi.spyOn(useArtifacts.getState(), "createVersion").mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "生成新版本" }));
  fireEvent.change(input, { target: { value: "等待时的新指令" } });
  await act(async () => resolve({ status: "success" }));
  expect(input).toHaveValue("等待时的新指令");
  vi.spyOn(useArtifacts.getState(), "createVersion").mockResolvedValue({
    status: "failed",
    error: "拒绝",
  });
  fireEvent.click(screen.getByRole("button", { name: "生成新版本" }));
  await waitFor(() => expect(input).toHaveValue("等待时的新指令"));
});
it("freezes a typed save source for the selected historical version", () => {
  const save = vi.fn();
  render(
    <ArtifactInspector
      inspector={{ kind: "artifact", objectId: "a", versionId: "v1" }}
      onSaveHtml={save}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "保存源码副本到空间" }));
  expect(save).toHaveBeenCalledWith({
    html: "<p>v1</p>",
    file_name: "测试_HTML-v1.html.txt",
    artifactId: "a",
    versionId: "v1",
    version: 1,
    title: "测试/HTML",
  });
});
it("verifies a task deep link against real work references and leaves the original reference fixed", () => {
  const work = workFixture({
    artifacts: [{ ...artifactFixture, artifact_id: "a", artifact_version_id: "v1" }],
  });
  useWorks.setState({ items: [work] });
  useShell.setState({
    route: {
      screen: "tasks",
      workId: work.work_id,
      inspector: { kind: "artifact", objectId: "a", versionId: "v2" },
    },
  });
  render(<ArtifactInspector inspector={useShell.getState().route.inspector!} />);
  expect(screen.getByText(/任务原引用 · r2 · v1/)).toBeVisible();
  expect(screen.getByText(/不自动替代任务交付/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /接受/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "返回任务成果与验收" }));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "task",
    objectId: "w1",
    tab: "outputs",
  });
  expect(work.artifacts[0]?.artifact_version_id).toBe("v1");
});
it("does not submit while IME composition is active, and retains drafts across closing", () => {
  const create = vi.spyOn(useArtifacts.getState(), "createVersion");
  const view = setup();
  const input = screen.getByLabelText("修改指令");
  fireEvent.change(input, { target: { value: "中文输入" } });
  fireEvent.compositionStart(input);
  fireEvent.submit(input.closest("form")!);
  expect(create).not.toHaveBeenCalled();
  view.unmount();
  setup();
  expect(screen.getByLabelText("修改指令")).toHaveValue("中文输入");
});
