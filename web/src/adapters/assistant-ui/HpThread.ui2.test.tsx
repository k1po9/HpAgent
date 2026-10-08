import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HpMessage } from "../../api/types";
import { useArtifacts } from "../../store/artifacts";
import { HpThread } from "./HpThread";
import { useConversationUi } from "../../store/conversationUi";

beforeEach(() => useConversationUi.getState().reset());
afterEach(cleanup);
const props = {
  messages: [],
  activeRun: null,
  attachments: [],
  fileUploadEnabled: false,
  sendDisabled: false,
  onFilesSelected: vi.fn(),
  onRemoveAttachment: vi.fn(),
  onCancel: vi.fn(),
};
const defer = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
describe("UI-2 controlled composer", () => {
  it("retains the real input on rejection and clears only accepted text", async () => {
    const onSend = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    render(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "中文草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(input).toHaveValue("中文草稿");
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(input).toHaveValue(""));
  });
  it("does not send during IME or Shift+Enter, then sends once after composition ends", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<HpThread {...props} onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "输入法" } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalledExactlyOnceWith("输入法"));
  });
  it("restores A→B→A drafts and does not clear a newer edit on late success", async () => {
    let resolve!: (ok: boolean) => void;
    const onSend = vi.fn(
      () =>
        new Promise<boolean>((r) => {
          resolve = r;
        }),
    );
    const { rerender } = render(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "旧稿" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(input, { target: { value: "下一条" } });
    rerender(<HpThread {...props} conversationKey="a:c2" onSend={onSend} />);
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: "B 草稿" } });
    resolve(true);
    rerender(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    await waitFor(() => expect(input).toHaveValue("下一条"));
  });
  it("allows B to send while A's submit promise is still pending", async () => {
    const a = defer<boolean>();
    const b = defer<boolean>();
    const onSend = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const { rerender } = render(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "A 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("A 消息");
    rerender(<HpThread {...props} conversationKey="a:c2" onSend={onSend} />);
    fireEvent.change(input, { target: { value: "B 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledTimes(2);
    expect(onSend).toHaveBeenNthCalledWith(2, "B 消息");
    b.resolve(false);
    a.resolve(false);
  });
  it("keeps the A submit lock when switching away and back", async () => {
    const a = defer<boolean>();
    const onSend = vi.fn().mockReturnValue(a.promise);
    const { rerender } = render(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "A 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledTimes(1);
    rerender(<HpThread {...props} conversationKey="a:c2" onSend={onSend} />);
    rerender(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledTimes(1);
    a.resolve(false);
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
  });
  it("does not let A completion release B's pending submit lock", async () => {
    const a = defer<boolean>();
    const b = defer<boolean>();
    const onSend = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const { rerender } = render(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "A 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    rerender(<HpThread {...props} conversationKey="a:c2" onSend={onSend} />);
    fireEvent.change(input, { target: { value: "B 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    a.resolve(true);
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledTimes(2);
    b.resolve(false);
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
  });
  it("releases only its own lock on rejected submit", async () => {
    const a = defer<boolean>();
    const b = defer<boolean>();
    const onSend = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const { rerender } = render(<HpThread {...props} conversationKey="a:c1" onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "A 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    rerender(<HpThread {...props} conversationKey="a:c2" onSend={onSend} />);
    fireEvent.change(input, { target: { value: "B 消息" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    a.reject(new Error("send failed"));
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeDisabled());
    b.resolve(false);
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
  });
});

it("offers HTML creation only for completed assistant messages with nonempty body", async () => {
  const load = vi.spyOn(useArtifacts.getState(), "loadForMessage").mockResolvedValue([]);
  const create = vi.spyOn(useArtifacts.getState(), "createArtifact");
  const base: HpMessage = {
    message_id: "empty",
    conversation_id: "c1",
    role: "assistant",
    status: "completed",
    content: " \n ",
    sequence: 1,
    client_request_id: null,
    produced_by_run_id: null,
    created_at: "2026-10-08T00:00:00Z",
    completed_at: null,
  };
  render(
    <HpThread
      {...props}
      onSend={vi.fn()}
      messages={[
        base,
        { ...base, message_id: "streaming", status: "pending", content: "部分正文", sequence: 2 },
        { ...base, message_id: "user", role: "user", content: "用户正文", sequence: 3 },
        {
          ...base,
          message_id: "accepted-assistant",
          status: "accepted",
          content: "尚未完成",
          sequence: 4,
        },
      ]}
    />,
  );
  expect(screen.queryByRole("button", { name: "生成 HTML" })).toBeNull();
  expect(load).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
  vi.restoreAllMocks();
});
