import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Theme } from "@radix-ui/themes";
import { ChatPane } from "./ChatPane";
import { useWorkbench } from "../store/workbench";
import { useShell } from "../store/shell";
import { useAuth } from "../store/auth";
import { useConversationUi } from "../store/conversationUi";
vi.mock("./conversation/ConversationResources", () => ({ ConversationResources: () => null }));
beforeEach(() => {
  useConversationUi.getState().reset();
  useWorkbench.getState().reset();
  useShell.getState().reset();
  useAuth.setState({
    status: "signedIn",
    account: { account_id: "a", status: "active", created_at: "2026-10-16T00:00:00Z" },
    capabilities: { file_upload: false, durable_agent: false },
  });
  useShell.setState({ route: { screen: "ai", conversationId: "c" } });
  useWorkbench.setState({
    activeConversationId: "c",
    activeConversation: {
      conversation_id: "c",
      title: "Loaded",
      status: "active",
      last_message_seq: 0,
      metadata_version: 1,
      created_at: "2026-10-16T00:00:00Z",
      updated_at: "2026-10-16T00:00:00Z",
    },
    loadingMessages: false,
    messages: [],
    activeRun: null,
  });
});
afterEach(cleanup);
const fold = () => screen.getByRole("button", { name: "收纳阅读区" });
it("invalidates a folded real ChatPane when capabilities change", () => {
  render(
    <Theme>
      <ChatPane />
    </Theme>,
  );
  fireEvent.click(fold());
  expect(screen.getByRole("button", { name: "展开阅读区" })).toBeVisible();
  act(() => useAuth.setState({ capabilities: { file_upload: true, durable_agent: false } }));
  expect(fold()).toHaveAttribute("aria-expanded", "true");
});
it("reveals and disables for unknown live facts without touching input draft", () => {
  render(
    <Theme>
      <ChatPane />
    </Theme>,
  );
  const input = screen.getByRole("textbox", { name: "消息输入" });
  fireEvent.change(input, { target: { value: "保留" } });
  fireEvent.click(fold());
  input.focus();
  act(() => useWorkbench.setState({ activeRunError: "sync unavailable" }));
  expect(fold()).toBeDisabled();
  expect(fold()).toHaveAttribute("aria-expanded", "true");
  expect(input).toHaveFocus();
  expect(input).toHaveValue("保留");
});
it("does not allow a mismatched/unknown conversation or auth state to fold", () => {
  useWorkbench.setState({ activeConversation: null });
  render(
    <Theme>
      <ChatPane />
    </Theme>,
  );
  expect(fold()).toBeDisabled();
  act(() => useAuth.setState({ status: "checking" }));
  expect(fold()).toBeDisabled();
});
it("reveals on sending and never refolds when sending settles", () => {
  render(
    <Theme>
      <ChatPane />
    </Theme>,
  );
  fireEvent.click(fold());
  act(() => useWorkbench.setState({ sending: true }));
  expect(fold()).toHaveAttribute("aria-expanded", "true");
  expect(fold()).toBeDisabled();
  act(() => useWorkbench.setState({ sending: false }));
  expect(fold()).toBeEnabled();
  expect(fold()).toHaveAttribute("aria-expanded", "true");
});
