import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationSidebar } from "../ConversationSidebar";
import { conversationDateGroup } from "./dateGroup";
import type { HpConversation } from "../../api/types";
afterEach(cleanup);
describe("UI-2 sidebar", () => {
  it("uses calendar days including yesterday across month boundaries", () => {
    const today = new Date(2026, 9, 1, 0, 10);
    expect(conversationDateGroup(new Date(2026, 9, 1).toISOString(), today)).toBe("今天");
    expect(conversationDateGroup(new Date(2026, 8, 30, 23, 59).toISOString(), today)).toBe("昨天");
    expect(conversationDateGroup(new Date(2026, 8, 25).toISOString(), today)).toBe("近 7 天");
    expect(conversationDateGroup(new Date(2026, 8, 24).toISOString(), today)).toBe("更早");
  });
  it("filters only loaded titles and leaves pagination available for an empty match", () => {
    const load = vi.fn();
    const conversation = {
      conversation_id: "c1",
      title: "已加载标题",
      updated_at: new Date().toISOString(),
    } as HpConversation;
    render(
      <ConversationSidebar
        conversations={[conversation]}
        activeConversationId="c1"
        creating={false}
        loading={false}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        hasMore
        onLoadMore={load}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "筛选已加载对话" }), {
      target: { value: "其他标题" },
    });
    expect(screen.getByText("已加载对话中没有匹配标题，仍可加载更多。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加载更多对话" }));
    expect(load).toHaveBeenCalledTimes(1);
  });
});
