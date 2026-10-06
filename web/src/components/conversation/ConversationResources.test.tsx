import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HpApi } from "../../api/resources";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { ConversationResources } from "./ConversationResources";

type Grant = Awaited<ReturnType<HpApi["listConversationResources"]>>["grants"][number];
const rule = (operation: Grant["operation"], grantId: string = operation): Grant => ({
  grant_id: grantId,
  node_id: "n1",
  name: "知识库",
  kind: "directory",
  operation,
  recursive: false,
});
const account = { account_id: "alice", status: "active", created_at: "2026-10-07T00:00:00Z" };
const defer = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
beforeEach(() => {
  useAuth.setState({ status: "signedIn", account, identities: null, capabilities: {} });
  useWorkbench.getState().reset();
  useWorkbench.setState({ activeConversationId: "c1" });
  useShell.getState().reset();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAuth.getState().expire();
  useWorkbench.getState().reset();
});
describe("UI-2 long-term resources", () => {
  it("shows only refreshed grants after a successful read-only authorization", async () => {
    let grants: Grant[] = [];
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(async () => ({
      grants,
      attachments: [],
    }));
    vi.spyOn(HpApi.prototype, "getWorkspace").mockResolvedValue({
      workspace_id: "w",
      root_id: "root",
      nodes: [
        {
          node_id: "n1",
          parent_id: "root",
          kind: "directory",
          name: "知识库",
          file_id: null,
          destination_id: null,
          revision: null,
          source: null,
        },
      ],
    });
    const grant = vi
      .spyOn(HpApi.prototype, "grantConversationResource")
      .mockImplementation(async (_id, _node, operations, recursive) => {
        grants = operations.map((op) => ({ ...rule(op), recursive }));
        return { grant_ids: grants.map((g) => g.grant_id) };
      });
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    await screen.findByText("暂无长期资料");
    fireEvent.click(screen.getByRole("button", { name: "使用资料" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "知识库 · 目录" }));
    fireEvent.click(screen.getByRole("button", { name: "确认读取授权" }));
    await screen.findByRole("button", { name: "撤销 知识库 的授权" });
    expect(grant).toHaveBeenCalledExactlyOnceWith(
      "c1",
      "n1",
      ["list_metadata", "read_content"],
      false,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("tracks partial revoke and retries only surviving rules", async () => {
    let grants = [rule("list_metadata"), rule("read_content"), rule("create_child")];
    let createChildFailed = false;
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(async () => ({
      grants,
      attachments: [],
    }));
    const revoke = vi
      .spyOn(HpApi.prototype, "revokeConversationResource")
      .mockImplementation(async (_id, grantId) => {
        if (grantId === "read_content") {
          grants = grants.filter((g) => g.grant_id !== grantId);
          throw new Error("offline");
        }
        if (grantId === "create_child" && !createChildFailed) {
          createChildFailed = true;
          throw new Error("offline");
        }
        grants = grants.filter((g) => g.grant_id !== grantId);
        return { affected_runs: [{ run_id: "r", stop_state: "stopping" }] };
      });
    vi.spyOn(HpApi.prototype, "getConversationDetail").mockRejectedValue(new Error("unused"));
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await screen.findByText(/1 条规则尚未撤销/);
    expect(screen.getByRole("status")).toHaveTextContent("正在停止");
    expect(within(screen.getByRole("dialog")).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(revoke.mock.calls.map((c) => c[1])).toEqual([
      "list_metadata",
      "read_content",
      "create_child",
      "create_child",
    ]);
  });
  it("closes revoke confirmation when DELETE is applied but its response is lost", async () => {
    let grants = [rule("list_metadata")];
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(async () => ({
      grants,
      attachments: [],
    }));
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockImplementation(
      async (_id, grantId) => {
        grants = grants.filter((g) => g.grant_id !== grantId);
        throw new Error("lost response");
      },
    );
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("暂无长期资料")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("授权已撤销");
  });
  it("keeps revoke context when DELETE and readback both fail, then narrows it after query recovery", async () => {
    let grants = [rule("list_metadata"), rule("read_content")];
    let calls = 0;
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(async () => {
      calls += 1;
      if (calls === 1 || calls > 2) {
        return { grants, attachments: [] };
      }
      throw new Error("readback failed");
    });
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockImplementation(
      async (_id, grantId) => {
        if (grantId === "list_metadata") grants = [rule("read_content")];
        throw new Error("offline");
      },
    );
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await screen.findByText(/状态待确认/);
    expect(within(screen.getByRole("dialog")).getAllByRole("listitem")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "重试资料查询" }));
    await waitFor(() =>
      expect(within(screen.getByRole("dialog")).getAllByRole("listitem")).toHaveLength(1),
    );
  });
  it("releases A's revoke busy state after switching away and back", async () => {
    const pending = defer<{ affected_runs: [] }>();
    let grantsById: Record<string, Grant[]> = { c1: [rule("list_metadata")], c2: [] };
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(
      async (conversationId) => ({
        grants: grantsById[conversationId] ?? [],
        attachments: [],
      }),
    );
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockImplementation(
      async (_id, grantId) => {
        grantsById = { ...grantsById, c1: grantsById.c1!.filter((g) => g.grant_id !== grantId) };
        return pending.promise;
      },
    );
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    expect(screen.getByRole("button", { name: "确认撤销" })).toBeDisabled();
    act(() => useWorkbench.setState({ activeConversationId: "c2" }));
    await screen.findByText("暂无长期资料");
    pending.resolve({ affected_runs: [] });
    await waitFor(() => expect(screen.getByRole("button", { name: "使用资料" })).toBeEnabled());
    act(() => useWorkbench.setState({ activeConversationId: "c1" }));
    await screen.findByText("暂无长期资料");
    expect(screen.getByRole("button", { name: "使用资料" })).toBeEnabled();
  });
  it("keeps A locked on return until its revoke finishes and refreshes grants", async () => {
    const pending = defer<{ affected_runs: [] }>();
    let grants = [rule("list_metadata")];
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(async () => ({
      grants,
      attachments: [],
    }));
    const revoke = vi
      .spyOn(HpApi.prototype, "revokeConversationResource")
      .mockImplementation(async () => {
        await pending.promise;
        grants = [];
        return { affected_runs: [] };
      });
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    act(() => useWorkbench.setState({ activeConversationId: "c2" }));
    act(() => useWorkbench.setState({ activeConversationId: "c1" }));
    expect(screen.getByRole("button", { name: "确认撤销" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    expect(revoke).toHaveBeenCalledTimes(1);
    pending.resolve({ affected_runs: [] });
    await screen.findByText("暂无长期资料");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "使用资料" })).toBeEnabled();
  });
  it("does not refresh the active run after an unmounted revoke completes", async () => {
    const pending = defer<{ affected_runs: [] }>();
    vi.spyOn(HpApi.prototype, "listConversationResources").mockResolvedValue({
      grants: [rule("list_metadata")],
      attachments: [],
    });
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockReturnValue(pending.promise);
    const refresh = vi.spyOn(useWorkbench.getState(), "refreshActiveRun");
    const view = render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    view.unmount();
    await act(async () => pending.resolve({ affected_runs: [] }));
    expect(refresh).not.toHaveBeenCalled();
  });
  it("keeps B busy when A's older revoke finishes first", async () => {
    const a = defer<{ affected_runs: [] }>();
    const b = defer<{ affected_runs: [] }>();
    let grantsById: Record<string, Grant[]> = {
      c1: [rule("list_metadata", "a-list")],
      c2: [rule("list_metadata", "b-list")],
    };
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(
      async (conversationId) => ({
        grants: grantsById[conversationId] ?? [],
        attachments: [],
      }),
    );
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockImplementation(
      async (conversationId, grantId) => {
        grantsById = {
          ...grantsById,
          [conversationId]: grantsById[conversationId]!.filter((g) => g.grant_id !== grantId),
        };
        return conversationId === "c1" ? a.promise : b.promise;
      },
    );
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    act(() => useWorkbench.setState({ activeConversationId: "c2" }));
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    a.resolve({ affected_runs: [] });
    await waitFor(() => expect(screen.getByRole("button", { name: "确认撤销" })).toBeDisabled());
    b.resolve({ affected_runs: [] });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("does not write an old revoke result into a new account session", async () => {
    const oldAccount = useAuth.getState().account;
    const nextAccount = { ...account };
    const pending = defer<{ affected_runs: [] }>();
    let grants = [rule("list_metadata")];
    vi.spyOn(HpApi.prototype, "listConversationResources").mockImplementation(async () => ({
      grants,
      attachments: [],
    }));
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockImplementation(
      async (_id, grantId) => {
        grants = grants.filter((g) => g.grant_id !== grantId);
        return pending.promise;
      },
    );
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销 知识库 的授权" }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    act(() => useAuth.setState({ account: null }));
    act(() => useAuth.setState({ account: nextAccount }));
    pending.resolve({ affected_runs: [] });
    await waitFor(() => expect(useAuth.getState().account).not.toBe(oldAccount));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("discards an A→B→A late query instead of showing stale grants", async () => {
    let resolve!: (page: { grants: Grant[]; attachments: [] }) => void;
    vi.spyOn(HpApi.prototype, "listConversationResources")
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      )
      .mockResolvedValue({ grants: [], attachments: [] });
    render(<ConversationResources refresh={0} ensure={async () => "c1"} />);
    act(() => useWorkbench.setState({ activeConversationId: "c2" }));
    act(() => useWorkbench.setState({ activeConversationId: "c1" }));
    await screen.findByText("暂无长期资料");
    await act(async () => resolve({ grants: [rule("read_content")], attachments: [] }));
    expect(screen.queryByRole("button", { name: "撤销 知识库 的授权" })).not.toBeInTheDocument();
  });
});
