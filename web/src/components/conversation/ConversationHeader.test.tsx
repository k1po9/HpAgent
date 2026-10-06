import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HpApi } from "../../api/resources";
import { HpCommandError, type HpConversation } from "../../api/types";
import { useWorkbench } from "../../store/workbench";
import { ConversationHeader } from "./ConversationHeader";
const conversation: HpConversation = {
  conversation_id: "c1",
  title: "原标题",
  status: "active",
  metadata_version: 1,
  last_message_seq: 0,
  created_at: "2026-10-07T00:00:00Z",
  updated_at: "2026-10-07T00:00:00Z",
};
beforeEach(() => {
  useWorkbench.getState().reset();
  useWorkbench.setState({ activeConversationId: "c1", activeConversation: conversation });
  vi.spyOn(HpApi.prototype, "listConversations").mockResolvedValue({
    items: [],
    has_more: false,
    next_cursor: null,
  });
});
afterEach(() => {
  cleanup();
  useWorkbench.getState().reset();
  vi.restoreAllMocks();
});
describe("UI-2 title optimistic concurrency", () => {
  it("keeps the edit on 412, reads the current version, and only saves on a new confirmation", async () => {
    const rename = vi
      .spyOn(HpApi.prototype, "renameConversation")
      .mockRejectedValueOnce(
        new HpCommandError(412, {
          code: "version_conflict",
          message: "conflict",
          retryable: false,
          request_id: "req",
          details: {},
        }),
      )
      .mockResolvedValueOnce({
        conversation: { ...conversation, title: "我的修改", metadata_version: 3 },
      });
    vi.spyOn(HpApi.prototype, "getConversationDetail").mockResolvedValue({
      conversation: { ...conversation, title: "外部修改", metadata_version: 2 },
      active_run: null,
    });
    render(<ConversationHeader />);
    fireEvent.click(screen.getByRole("button", { name: "修改标题" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "我的修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存标题" }));
    await waitFor(() =>
      expect(useWorkbench.getState().activeConversation?.metadata_version).toBe(2),
    );
    expect(screen.getByRole("textbox")).toHaveValue("我的修改");
    expect(rename).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "保存标题" }));
    await screen.findByRole("heading", { name: "我的修改" });
    expect(rename.mock.calls[1]?.[2]).toBe('"conversation-c1-m2"');
  });
  it("reads back an uncertain PATCH instead of automatically retrying it", async () => {
    const rename = vi
      .spyOn(HpApi.prototype, "renameConversation")
      .mockRejectedValue(new TypeError("response lost"));
    vi.spyOn(HpApi.prototype, "getConversationDetail").mockResolvedValue({
      conversation: { ...conversation, title: "已保存", metadata_version: 2 },
      active_run: null,
    });
    render(<ConversationHeader />);
    fireEvent.click(screen.getByRole("button", { name: "修改标题" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "已保存" } });
    fireEvent.click(screen.getByRole("button", { name: "保存标题" }));
    await screen.findByRole("heading", { name: "已保存" });
    expect(rename).toHaveBeenCalledTimes(1);
  });
});
