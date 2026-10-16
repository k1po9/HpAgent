import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Theme } from "@radix-ui/themes";
import type { HpMessage, HpRun } from "../../api/types";
import { useConversationUi } from "../../store/conversationUi";
import { HpThread, type HpThreadProps } from "./HpThread";

const messages: HpMessage[] = [
  {
    message_id: "m1",
    conversation_id: "c1",
    role: "user",
    status: "completed",
    content: "Long reading",
    sequence: 1,
    client_request_id: null,
    produced_by_run_id: null,
    created_at: "2026-10-16T00:00:00Z",
    completed_at: null,
  },
];
const defaults: HpThreadProps = {
  conversationKey: "account:c1",
  messages,
  activeRun: null,
  attachments: [
    {
      localId: "a1",
      fileId: "f1",
      name: "note.md",
      size: 7,
      file: null,
      error: null,
      status: "ready",
    },
  ],
  fileUploadEnabled: true,
  sendDisabled: false,
  onFilesSelected: vi.fn(),
  onRemoveAttachment: vi.fn(),
  onCancel: vi.fn(),
  onSend: vi.fn().mockResolvedValue(false),
};
const run = (status: string) => ({ run_id: "r1", status }) as HpRun;
function thread(props: Partial<HpThreadProps> = {}) {
  return (
    <Theme>
      <HpThread {...defaults} {...props} />
    </Theme>
  );
}
const fold = () => fireEvent.click(screen.getByRole("button", { name: "收纳阅读区" }));
const expand = () => fireEvent.click(screen.getByRole("button", { name: "展开阅读区" }));
beforeEach(() => {
  useConversationUi.getState().reset();
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("R5.0 reading presentation", () => {
  it("keeps real message, input and attachment nodes, draft, URL and callbacks through repeated toggles", async () => {
    const { container } = render(thread());
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "草稿 draft" } });
    await waitFor(() => expect(container.querySelector('[data-message-id="m1"]')).not.toBeNull());
    const msg = container.querySelector('[data-message-id="m1"]');
    const attachment = screen.getByRole("button", { name: "移除 note.md" });
    const url = location.href;
    for (let i = 0; i < 5; i++) {
      fold();
      expect(container.querySelector(".hp-reading")).toHaveAttribute("hidden");
      expect(container.querySelector(".hp-reading")).toHaveAttribute("inert");
      expect(screen.getByRole("button", { name: "展开阅读区" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
      expect(screen.queryByText("Long reading")).not.toBeVisible();
      expect(input).toBeVisible();
      expect(attachment).toBeVisible();
      expand();
      expect(container.querySelector('[data-message-id="m1"]')).toBe(msg);
      expect(screen.getByRole("textbox", { name: "消息输入" })).toBe(input);
      expect(screen.getByRole("button", { name: "移除 note.md" })).toBe(attachment);
    }
    expect(input).toHaveValue("草稿 draft");
    expect(location.href).toBe(url);
    expect(defaults.onSend).not.toHaveBeenCalled();
    expect(defaults.onCancel).not.toHaveBeenCalled();
  });
  it.each(["queued", "running", "cancelling", "awaiting_approval", "future_status"])(
    "blocks nonterminal/unknown %s independent of animation",
    (status) => {
      render(thread({ activeRun: run(status) }));
      expect(screen.getByRole("button", { name: "收纳阅读区" })).toBeDisabled();
    },
  );
  it.each(["succeeded", "failed", "cancelled"])("allows authoritative terminal %s", (status) => {
    render(thread({ activeRun: run(status) }));
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toBeEnabled();
    fold();
    expect(screen.getByRole("button", { name: "展开阅读区" })).toBeEnabled();
  });
  it.each([
    { loadingHistory: true },
    { loadingMoreMessages: true },
    { sendUncertain: true },
    { stopping: true },
    { readingBlockedReason: "状态未知" },
  ])("fails closed during %j", (props) => {
    render(thread(props));
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toBeDisabled();
  });
  it("reveals before failed send, retains draft/attachment/focus and sends exactly once", async () => {
    const onSend = vi.fn().mockResolvedValue(false);
    render(thread({ onSend }));
    fold();
    const input = screen.getByRole("textbox", { name: "消息输入" });
    input.focus();
    fireEvent.change(input, { target: { value: "retry later" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalledExactlyOnceWith("retry later"));
    expect(screen.getByText("Long reading")).toBeVisible();
    expect(input).toHaveFocus();
    expect(input).toHaveValue("retry later");
    expect(screen.getByRole("button", { name: "移除 note.md" })).toBeVisible();
  });
  it("does not reveal or send an IME/attachment-ineligible submit; reveals after valid composition", async () => {
    const onSend = vi.fn().mockResolvedValue(false);
    const { rerender } = render(thread({ onSend, sendDisabled: true }));
    fold();
    const input = screen.getByRole("textbox", { name: "消息输入" });
    fireEvent.change(input, { target: { value: "输入" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "展开阅读区" })).toBeVisible();
    rerender(thread({ onSend }));
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "展开阅读区" })).toBeVisible();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalledOnce());
  });
  it("reveals a late reply and recovered/uncertain run without moving input focus", () => {
    const { rerender } = render(thread());
    fold();
    const input = screen.getByRole("textbox", { name: "消息输入" });
    input.focus();
    rerender(
      thread({
        messages: [
          ...messages,
          { ...messages[0]!, message_id: "m2", content: "late reply", sequence: 2 },
        ],
      }),
    );
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(input).toHaveFocus();
    fold();
    rerender(thread({ messages: [...messages], activeRun: run("running") }));
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toBeDisabled();
    expect(input).toHaveFocus();
  });
  it("invalidates folded state across session/account/permission scope changes and never resurrects it", () => {
    const { rerender } = render(thread({ readingScope: "a:c1:ai:permission1" }));
    fold();
    rerender(thread({ readingScope: "a:c1:workspace:permission1" }));
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    rerender(thread({ readingScope: "a:c1:ai:permission1" }));
    fold();
    rerender(thread({ readingScope: "b:c1:ai:permission1" }));
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    fold();
    rerender(thread({ readingScope: "b:c1:ai:permission2" }));
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
  it("ignores hidden scroll events and restores message-relative offset after changed geometry", async () => {
    const { container } = render(thread());
    await waitFor(() => expect(container.querySelector('[data-message-id="m1"]')).not.toBeNull());
    const viewport = container.querySelector(".hp-thread__viewport") as HTMLDivElement;
    const msg = container.querySelector('[data-message-id="m1"]') as HTMLElement;
    Object.defineProperties(viewport, {
      scrollHeight: { value: 2000 },
      clientHeight: { value: 400 },
    });
    viewport.getBoundingClientRect = () => ({ top: 100 }) as DOMRect;
    let top = -20;
    msg.getBoundingClientRect = () => ({ top, bottom: top + 500 }) as DOMRect;
    viewport.scrollTop = 600;
    fireEvent.scroll(viewport);
    const saved = { ...useConversationUi.getState().entries["account:c1"] };
    fold();
    viewport.scrollTop = 0;
    fireEvent.scroll(viewport);
    expect(useConversationUi.getState().entries["account:c1"]).toEqual(saved);
    top = 580;
    expand();
    expect(viewport.scrollTop).toBe(600);
  });
  it("keeps a permanent portal control and forwards focus out of a programmatically hidden reading control", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const { container, unmount } = render(thread({ readingControlsHost: host }));
    await waitFor(() => expect(container.querySelector('[data-message-id="m1"]')).not.toBeNull());
    const link = document.createElement("button");
    container.querySelector(".hp-thread__viewport")!.append(link);
    link.focus();
    fold();
    const control = screen.getByRole("button", { name: "展开阅读区" });
    expect(control.parentElement).toBe(host);
    expect(control).toHaveFocus();
    expand();
    expect(screen.getByRole("button", { name: "收纳阅读区" })).toBe(control);
    unmount();
    expect(host.childElementCount).toBe(0);
    host.remove();
  });
  it("does not let a late failed submission reset a newly folded different conversation", async () => {
    let resolve!: (ok: boolean) => void;
    const onSend = vi.fn(
      () =>
        new Promise<boolean>((r) => {
          resolve = r;
        }),
    );
    const { rerender } = render(thread({ onSend }));
    fireEvent.change(screen.getByRole("textbox", { name: "消息输入" }), { target: { value: "A" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    rerender(thread({ onSend, conversationKey: "account:c2", readingScope: "account:c2" }));
    fold();
    await act(async () => resolve(false));
    expect(screen.getByRole("button", { name: "展开阅读区" })).toBeVisible();
  });
  it("forwards focus from reading controls outside the viewport as well", () => {
    render(thread({ hasMoreMessages: true, onLoadMoreMessages: vi.fn() }));
    const load = screen.getByRole("button", { name: "加载更早的消息" });
    load.focus();
    fold();
    expect(screen.getByRole("button", { name: "展开阅读区" })).toHaveFocus();
    expect(load).not.toBeVisible();
  });
});
