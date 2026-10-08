import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ArtifactCreateForm } from "./ArtifactCreateForm";
import { useArtifacts, type CommandResult } from "../../store/artifacts";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import type { HpMessage } from "../../api/types";
const artifact = {
  artifact_id: "a",
  conversation_id: "c",
  source_message_id: "m",
  kind: "html" as const,
  title: "成果",
  created_at: "now",
  updated_at: "now",
};
function deferred() {
  let resolve!: (s: CommandResult) => void;
  const promise = new Promise<CommandResult>((r) => {
    resolve = r;
  });
  return { resolve, promise };
}
beforeEach(() => {
  vi.restoreAllMocks();
  useShell.getState().reset();
  useArtifacts.getState().reset();
  useWorkbench.setState({
    activeConversationId: "c",
    messages: [
      { message_id: "m", role: "assistant", status: "completed", content: "完成回复", sequence: 1 },
      { message_id: "n", role: "assistant", status: "completed", content: "另一回复", sequence: 2 },
      { message_id: "empty", role: "assistant", status: "completed", content: " ", sequence: 3 },
      {
        message_id: "busy",
        role: "assistant",
        status: "running",
        content: "流式回复",
        sequence: 4,
      },
    ] as HpMessage[],
  });
  vi.spyOn(useArtifacts.getState(), "loadForMessage").mockResolvedValue([]);
});
function submit() {
  fireEvent.submit(screen.getByLabelText("来源消息").closest("form")!);
}
it("preserves initial instructions, ignores empty/incomplete sources and serializes submits", async () => {
  const pending = deferred();
  const create = vi
    .spyOn(useArtifacts.getState(), "createArtifact")
    .mockReturnValue(pending.promise);
  render(<ArtifactCreateForm />);
  expect(screen.getAllByRole("option")).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("来源消息"), { target: { value: "m" } });
  fireEvent.change(screen.getByLabelText("生成要求"), { target: { value: "使用中文标题" } });
  submit();
  submit();
  expect(create).toHaveBeenCalledTimes(1);
  expect(create).toHaveBeenCalledWith("m", "使用中文标题");
  fireEvent.change(screen.getByLabelText("生成要求"), { target: { value: "在途编辑" } });
  await act(async () => pending.resolve({ status: "success", artifact }));
  expect(useShell.getState().route.inspector).toMatchObject({
    objectId: "a",
    origin: { messageId: "m", conversationId: "c" },
  });
  expect(screen.getByLabelText("生成要求")).toHaveValue("在途编辑");
});
it.each(["source", "close", "navigate"])("does not steal selection after %s", async (action) => {
  const pending = deferred();
  vi.spyOn(useArtifacts.getState(), "createArtifact").mockReturnValue(pending.promise);
  const view = render(<ArtifactCreateForm />);
  fireEvent.change(screen.getByLabelText("来源消息"), { target: { value: "m" } });
  submit();
  if (action === "source")
    fireEvent.change(screen.getByLabelText("来源消息"), { target: { value: "n" } });
  else if (action === "close") view.unmount();
  else act(() => useShell.getState().navigate({ screen: "tasks" }));
  await act(async () => pending.resolve({ status: "success", artifact }));
  expect(useShell.getState().route.inspector).toBeUndefined();
});
it("shows uncertain intent recovery and keeps error and input associated", () => {
  useArtifacts.setState({
    intents: {
      "message:m": {
        key: "fixed",
        instruction: "要求",
        busy: false,
        uncertain: true,
        result: { status: "uncertain", error: "连接中断" },
      },
    },
  });
  render(<ArtifactCreateForm />);
  fireEvent.change(screen.getByLabelText("来源消息"), { target: { value: "m" } });
  expect(screen.getByRole("button", { name: "恢复原 HTML 生成" })).toBeEnabled();
  expect(screen.getByLabelText("来源消息")).toHaveAttribute(
    "aria-describedby",
    "artifact-create-error",
  );
  expect(screen.getByRole("alert")).toHaveTextContent("连接中断");
});
