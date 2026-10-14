import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ExecutionBlock } from "./ExecutionBlock";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { useRunInspector, runApi } from "../../store/runInspector";
import type { HpChatRunSnapshot } from "../../api/types";
const initialAuth = useAuth.getState();
const data = (id = "A", conversation = "chat") =>
  ({
    source_kind: "chat",
    run: {
      run_id: id,
      conversation_id: conversation,
      status: "succeeded",
      version: 1,
      agent_strategy: "react",
      failure: null,
    },
    assistant_message: {},
  }) as HpChatRunSnapshot;
beforeEach(() => {
  useWorkbench.getState().reset();
  useRunInspector.getState().reset();
  useWorkbench.setState({ activeConversationId: "chat" });
  useAuth.setState({ status: "signedIn", account: { account_id: "one" } as never });
});
afterEach(() => {
  useAuth.setState(initialAuth);
  vi.restoreAllMocks();
});
it("uses only the matching historical Run facts", async () => {
  vi.spyOn(runApi, "getRun").mockResolvedValue(data());
  render(<ExecutionBlock runId="A" />);
  await waitFor(() => expect(screen.getByText("已完成")).toBeVisible());
  expect(screen.getByText("对话执行 · ReAct")).toBeVisible();
  expect(screen.queryByTestId("stop-run")).toBeNull();
});
it.each([data("B"), data("A", "another")])(
  "rejects another Run or conversation response",
  async (snapshot) => {
    const get = vi.spyOn(runApi, "getRun").mockResolvedValue(snapshot);
    render(<ExecutionBlock runId="A" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(get).toHaveBeenCalledOnce();
    expect(screen.getByText("状态待同步")).toBeVisible();
    expect(screen.queryByText("已完成")).toBeNull();
  },
);
it("aborts account-old requests and ignores their late result", async () => {
  let finish!: (snapshot: HpChatRunSnapshot) => void;
  const get = vi
    .spyOn(runApi, "getRun")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockImplementation(() => new Promise(() => {}));
  render(<ExecutionBlock runId="A" />);
  const signal = get.mock.calls[0]![1]!;
  act(() => useAuth.setState({ account: { account_id: "two" } as never }));
  expect(signal.aborted).toBe(true);
  await act(async () => finish(data()));
  expect(screen.getByText("状态待同步")).toBeVisible();
  expect(screen.queryByText("已完成")).toBeNull();
});
