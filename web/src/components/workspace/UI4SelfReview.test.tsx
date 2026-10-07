import { StrictMode } from "react";
import { api } from "../../api/client";
import { listGrants } from "./workspaceOperations";
import { GrantEditor } from "./GrantEditor";
import { useVersionOperations } from "./versionOperations";
import { HpCommandError } from "../../api/types";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell } from "../../store/shell";
import { useWorkbench } from "../../store/workbench";
import { useWorkspaceQuery } from "./useWorkspaceQuery";
import { FileVersions } from "./FileVersions";
import { WorkspaceScreen } from "./WorkspaceScreen";
import { resetWorkspaceOperations, useAffectedRuns } from "./workspaceOperations";
import { useTraceStore } from "../trace/traceStore";
import { HpApi } from "../../api/resources";
import { useAuth } from "../../store/auth";
import { ConversationResources } from "../conversation/ConversationResources";
import type { HpWorkspaceNode } from "../../api/types";

const root: HpWorkspaceNode = {
  node_id: "root",
  parent_id: null,
  kind: "directory",
  name: "",
  file_id: null,
  destination_id: null,
  revision: null,
  source: null,
};
const node: HpWorkspaceNode = {
  ...root,
  node_id: "n",
  parent_id: "root",
  kind: "file",
  name: "notes.txt",
  file_id: "f",
  destination_id: "dest",
  revision: 1,
};
const tree = { workspace_id: "w", root_id: "root", nodes: [root, node] };
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((r, fail) => {
    resolve = r;
    reject = fail;
  });
  return { promise, resolve, reject };
}
beforeEach(() => {
  vi.restoreAllMocks();
  useWorkspace.getState().reset();
  resetWorkspaceOperations();
  useShell.getState().reset();
  useTraceStore.getState().reset();
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue(tree);
  vi.spyOn(useWorkbench.getState(), "refreshActiveRun").mockResolvedValue();
});
afterEach(() => {
  cleanup();
  resetWorkspaceOperations();
});

it("R1: concurrent consumers of the same query both receive the result with one request", async () => {
  const pending = deferred<string>();
  const load = vi.fn(() => pending.promise);
  function Consumer({ id }: { id: string }) {
    const q = useWorkspaceQuery("grants:conversation:c", load);
    return <p data-testid={id}>{q.error ?? q.data ?? "loading"}</p>;
  }
  render(
    <>
      <Consumer id="first" />
      <Consumer id="second" />
    </>,
  );
  await waitFor(() => expect(load).toHaveBeenCalled());
  await act(async () => pending.resolve("rules"));
  expect.soft(load).toHaveBeenCalledTimes(1);
  expect.soft(screen.getByTestId("first")).toHaveTextContent("rules");
  expect.soft(screen.getByTestId("second")).toHaveTextContent("rules");
});

it("R2: an uncertain version submission retains its operation key across tab unmount/remount", async () => {
  vi.spyOn(workspaceApi, "getWorkspaceVersions").mockResolvedValue({
    current: { node_id: "n", destination_id: "dest", revision: 1, file_id: "f", sha256: "sha" },
    revisions: [],
  });
  vi.spyOn(workspaceApi, "listRunPublishedFiles").mockResolvedValue({
    files: [{ file_id: "out", name: "out.txt", sha256: "out-sha" }],
  });
  const update = vi
    .spyOn(workspaceApi, "updateWorkspaceFile")
    .mockRejectedValue(new Error("response lost"));
  async function submit() {
    fireEvent.change(await screen.findByLabelText("编辑 Run ID"), { target: { value: "run" } });
    fireEvent.click(screen.getByRole("button", { name: "选择 Run 已发布输出" }));
    await screen.findByRole("option", { name: "out.txt" });
    fireEvent.change(screen.getByLabelText("Run 已发布输出"), { target: { value: "out" } });
    fireEvent.click(screen.getByRole("button", { name: "提交新版本" }));
    await screen.findByText("response lost");
  }
  const first = render(<FileVersions node={node} />);
  await submit();
  first.unmount(); // FileBody conditionally unmounts FileVersions on a tab switch.
  render(<FileVersions node={node} />);
  fireEvent.click(await screen.findByRole("button", { name: "继续原版本提交" }));
  await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]);
});

it("R3: confirmed Composer revoke invalidates diagnostics and follows affected Runs despite read-back failure", async () => {
  const grant = {
    grant_id: "g",
    node_id: "n",
    name: "notes",
    kind: "file" as const,
    operation: "read_content" as const,
    recursive: false,
  };
  useAuth.setState({
    status: "signedIn",
    account: { account_id: "alice", status: "active", created_at: "" },
  });
  useWorkbench.setState({ activeConversationId: "c" });
  useTraceStore.setState({
    modelInputs: {
      snapshot: { status: "loaded", detail: { marker: "synthetic cached body" } as never },
    },
  });
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  vi.spyOn(HpApi.prototype, "listConversationResources")
    .mockResolvedValueOnce({ grants: [grant], attachments: [] })
    .mockRejectedValue(new Error("readback offline"));
  const revoke = vi
    .spyOn(HpApi.prototype, "revokeConversationResource")
    .mockResolvedValue({ affected_runs: [{ run_id: "affected", stop_state: "stopping" }] });
  render(<ConversationResources refresh={0} ensure={async () => "c"} />);
  fireEvent.click(await screen.findByRole("button", { name: "撤销 notes 的授权" }));
  fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
  await screen.findByText("状态待确认，请重新查询授权。");
  expect(revoke).toHaveBeenCalledTimes(1);
  expect.soft(useTraceStore.getState().modelInputs).toEqual({});
  expect.soft(useAffectedRuns.getState().states).toHaveProperty("affected");
});

it("R4: navigating a valid breadcrumb must not claim that its directory is unavailable", async () => {
  vi.spyOn(workspaceApi, "getWorkspaceSpace").mockResolvedValue({
    physical_files: 1,
    physical_bytes: 1,
    files_with_active_entry: 1,
    bytes_with_active_entry: 1,
  });
  useShell.setState({ route: { screen: "workspace", directoryId: "root" } });
  render(<WorkspaceScreen />);
  fireEvent.click(await screen.findByRole("button", { name: "根目录" }));
  expect(screen.queryByText("目录不存在或不可访问，已返回根目录。")).not.toBeInTheDocument();
});

it("R1: two StrictMode GrantEditors converge through concurrent subscription and forced refresh", async () => {
  useWorkspace.setState({ tree });
  const initial = deferred<{ grants: never[]; attachments: never[] }>();
  const superseded = deferred<{ grants: never[]; attachments: never[] }>();
  const read = vi
    .spyOn(api, "request")
    .mockReturnValueOnce(initial.promise)
    .mockReturnValueOnce(superseded.promise)
    .mockResolvedValue({ grants: [], attachments: [] });
  const subject = { kind: "work" as const, id: "w1", title: "W" };
  render(
    <StrictMode>
      <GrantEditor node={node} subject={subject} />
      <GrantEditor node={node} subject={subject} />
    </StrictMode>,
  );
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  await act(async () => initial.resolve({ grants: [], attachments: [] }));
  expect(screen.getAllByText("所选主体暂无适用规则。")).toHaveLength(2);
  const key = "grants:work:w1";
  let old!: Promise<unknown>;
  await act(async () => {
    old = useWorkspace
      .getState()
      .query(key, () => listGrants(subject), true)
      .catch(() => {});
  });
  await act(async () => {
    await useWorkspace.getState().query(key, () => listGrants(subject), true);
  });
  await act(async () => {
    superseded.resolve({ grants: [], attachments: [] });
    await old;
  });
  expect(read).toHaveBeenCalledTimes(3);
  expect(screen.getAllByText("所选主体暂无适用规则。")).toHaveLength(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("R1: A → B → A joins the live flight and account reset refuses its late result", async () => {
  const old = deferred<string>();
  const loadA = vi.fn(() => old.promise);
  const loadB = vi.fn(async () => "B");
  function Consumer({ queryKey }: { queryKey: string }) {
    const q = useWorkspaceQuery(queryKey, queryKey === "A" ? loadA : loadB);
    return <p>{q.error ?? q.data ?? "loading"}</p>;
  }
  const view = render(<Consumer queryKey="A" />);
  await waitFor(() => expect(loadA).toHaveBeenCalledTimes(1));
  view.rerender(<Consumer queryKey="B" />);
  await screen.findByText("B");
  view.rerender(<Consumer queryKey="A" />);
  expect(loadA).toHaveBeenCalledTimes(1);
  const next = deferred<string>();
  loadA.mockImplementation(() => next.promise);
  act(() => useWorkspace.getState().reset());
  await waitFor(() => expect(loadA).toHaveBeenCalledTimes(2));
  await act(async () => old.resolve("old account"));
  expect(screen.queryByText("old account")).not.toBeInTheDocument();
  await act(async () => next.resolve("new account"));
  await screen.findByText("new account");
});

function prepareVersions() {
  const read = vi.spyOn(workspaceApi, "getWorkspaceVersions").mockResolvedValue({
    current: { node_id: "n", destination_id: "dest", revision: 1, file_id: "f", sha256: "sha" },
    revisions: [],
  });
  vi.spyOn(workspaceApi, "listRunPublishedFiles").mockResolvedValue({
    files: [{ file_id: "out", name: "out.txt", sha256: "new-sha" }],
  });
  return read;
}
async function selectAndSubmitVersion() {
  fireEvent.change(await screen.findByLabelText("编辑 Run ID"), { target: { value: "run" } });
  fireEvent.click(screen.getByRole("button", { name: "选择 Run 已发布输出" }));
  await screen.findByRole("option", { name: "out.txt" });
  fireEvent.change(screen.getByLabelText("Run 已发布输出"), { target: { value: "out" } });
  fireEvent.click(screen.getByRole("button", { name: "提交新版本" }));
}

it("R2: pending submission survives closing, then replays the frozen CAS after metadata changes", async () => {
  const read = prepareVersions();
  const pending = deferred<never>();
  const update = vi
    .spyOn(workspaceApi, "updateWorkspaceFile")
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValue({ revision: 2 } as never);
  const view = render(<FileVersions node={node} />);
  await selectAndSubmitVersion();
  expect(update).toHaveBeenCalledTimes(1);
  view.unmount();
  render(<FileVersions node={node} />);
  expect(screen.getByRole("button", { name: "继续原版本提交" })).toBeDisabled();
  expect(screen.getByLabelText("编辑 Run ID")).toBeDisabled();
  // The operation, not the view, owns completion and its unresolved identity.
  await act(async () => pending.reject(new Error("response lost")));
  await screen.findByText("response lost");
  read.mockResolvedValue({
    current: {
      node_id: "n",
      destination_id: "dest",
      revision: 2,
      file_id: "new",
      sha256: "changed",
    },
    revisions: [],
  });
  act(() => useWorkspace.getState().invalidateQueries());
  await screen.findByText(/当前版本：2/);
  fireEvent.click(screen.getByRole("button", { name: "继续原版本提交" }));
  await waitFor(() =>
    expect(useVersionOperations.getState().operations.n?.state).toBe("succeeded"),
  );
  expect(update.mock.calls[1]).toEqual(update.mock.calls[0]);
});

it("R2: a definite CAS rejection requires confirmation before a fresh key and reset drops late completion", async () => {
  const read = prepareVersions();
  const update = vi.spyOn(workspaceApi, "updateWorkspaceFile").mockRejectedValueOnce(
    new HpCommandError(409, {
      code: "workspace_version_conflict",
      message: "conflict",
      request_id: null,
      retryable: false,
      details: {},
    }),
  );
  render(<FileVersions node={node} />);
  await selectAndSubmitVersion();
  await screen.findByRole("button", { name: "读回当前版本并重新确认" });
  const first = update.mock.calls[0]!;
  read.mockResolvedValue({
    current: {
      node_id: "n",
      destination_id: "dest",
      revision: 2,
      file_id: "new",
      sha256: "changed",
    },
    revisions: [],
  });
  fireEvent.click(screen.getByRole("button", { name: "读回当前版本并重新确认" }));
  await screen.findByText(/当前版本：2/);
  expect(update).toHaveBeenCalledTimes(1);
  const pending = deferred<never>();
  update.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole("button", { name: "提交新版本" }));
  expect(update.mock.calls[1]?.[3]).toBe(2);
  expect(update.mock.calls[1]?.[5]).not.toBe(first[5]);
  act(() => {
    useWorkspace.getState().reset();
    resetWorkspaceOperations();
  });
  await act(async () => pending.resolve({ revision: 3 } as never));
  expect(useVersionOperations.getState().operations).toEqual({});
  expect(useWorkspace.getState().tree).toBeNull();
});

const grant = {
  grant_id: "g",
  node_id: "n",
  name: "notes",
  kind: "file" as const,
  operation: "read_content" as const,
  recursive: false,
};
function seedRevokeView() {
  useAuth.setState({
    status: "signedIn",
    account: { account_id: "alice", status: "active", created_at: "" },
  });
  useWorkbench.setState({ activeConversationId: "c" });
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  useWorkspace.setState({ cache: { "grants:conversation:c": { value: [grant], loading: false } } });
  useTraceStore.setState({
    modelInputs: { snapshot: { status: "loaded", detail: { marker: "old body" } as never } },
  });
}
async function confirmRevoke() {
  fireEvent.click(await screen.findByRole("button", { name: "撤销 notes 的授权" }));
  fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
}

it("R3: partial success invalidates immediately and rejects a late Model Input while readback fails", async () => {
  seedRevokeView();
  const other = { ...grant, grant_id: "g2", operation: "list_metadata" as const };
  vi.spyOn(HpApi.prototype, "listConversationResources")
    .mockResolvedValueOnce({ grants: [grant, other], attachments: [] })
    .mockRejectedValue(new Error("readback offline"));
  const waiting = deferred<{ affected_runs: [] }>();
  vi.spyOn(HpApi.prototype, "revokeConversationResource")
    .mockResolvedValueOnce({ affected_runs: [{ run_id: "affected", stop_state: "stopping" }] })
    .mockReturnValueOnce(waiting.promise);
  const body = deferred<never>();
  vi.spyOn(HpApi.prototype, "getModelInput").mockReturnValue(body.promise);
  const late = useTraceStore.getState().loadModelInput("pending-body");
  render(<ConversationResources refresh={0} ensure={async () => "c"} />);
  await confirmRevoke();
  await waitFor(() => expect(useAffectedRuns.getState().states).toHaveProperty("affected"));
  expect(useTraceStore.getState().modelInputs).toEqual({});
  expect(useWorkspace.getState().cache).toEqual({});
  await act(async () => {
    body.resolve({ marker: "stale body" } as never);
    await late;
    waiting.reject(new Error("DELETE lost"));
  });
  await screen.findByText("状态待确认，请重新查询授权。");
  expect(useTraceStore.getState().modelInputs).toEqual({});
});

it.each(["view", "account", "refresh"] as const)(
  "R3: a delayed revoke after changing %s only affects the originating account",
  async (change) => {
    seedRevokeView();
    vi.spyOn(HpApi.prototype, "listConversationResources").mockResolvedValue({
      grants: [grant],
      attachments: [],
    });
    const pending = deferred<{
      affected_runs: Array<{ run_id: string; stop_state: "stopping" }>;
    }>();
    vi.spyOn(HpApi.prototype, "revokeConversationResource").mockReturnValue(pending.promise);
    const view = render(<ConversationResources refresh={0} ensure={async () => "c"} />);
    await confirmRevoke();
    view.unmount();
    useWorkbench.setState({ activeConversationId: "other-view" });
    if (change === "account") {
      useAuth.setState({ account: { account_id: "bob", status: "active", created_at: "" } });
      useWorkspace.getState().reset();
      resetWorkspaceOperations();
      useTraceStore.setState({ modelInputs: { bob: { status: "loaded", detail: {} as never } } });
    }
    if (change === "refresh") useAuth.setState({ account: { ...useAuth.getState().account! } });
    await act(async () =>
      pending.resolve({ affected_runs: [{ run_id: "affected", stop_state: "stopping" }] }),
    );
    if (change === "account") {
      expect(useAffectedRuns.getState().states).toEqual({});
      expect(useTraceStore.getState().modelInputs).toHaveProperty("bob");
    } else {
      expect(useAffectedRuns.getState().states).toHaveProperty("affected");
      expect(useTraceStore.getState().modelInputs).toEqual({});
      expect(useWorkbench.getState().refreshActiveRun).not.toHaveBeenCalled();
    }
  },
);

it.each([false, true])(
  "R4: invalid/deleted directory normalization waits for accepted navigation (draft=%s)",
  async (draft) => {
    vi.spyOn(workspaceApi, "getWorkspaceSpace").mockResolvedValue({
      physical_files: 0,
      physical_bytes: 0,
      files_with_active_entry: 0,
      bytes_with_active_entry: 0,
    });
    const directory = { ...root, node_id: "dir", parent_id: "root", name: "Directory" };
    vi.mocked(workspaceApi.getWorkspace).mockResolvedValue({ ...tree, nodes: [root, directory] });
    useWorkspace.setState({ tree: { ...tree, nodes: [root, directory] } });
    useShell.setState({ route: { screen: "workspace", directoryId: "dir" } });
    render(<WorkspaceScreen />);
    await screen.findByRole("button", { name: "Directory" });
    fireEvent.click(screen.getByRole("button", { name: "Directory" }));
    expect(useWorkspace.getState().notice).toBe("");
    if (draft) useShell.setState({ dirtyResourceEditor: "draft" });
    act(() => useWorkspace.setState({ tree })); // A fresh tree no longer contains the directory.
    if (draft) {
      expect(useShell.getState().route.directoryId).toBe("dir");
      expect(useWorkspace.getState().notice).toBe("");
      const navigate = useShell.getState().pendingResourceChange!;
      act(() => {
        useShell.setState({ dirtyResourceEditor: null, pendingResourceChange: null });
        navigate();
      });
    }
    await screen.findByText("目录不存在或不可访问，已返回根目录。");
    expect(useShell.getState().route.directoryId).toBe("root");
    expect(window.location.hash).toBe("#/workspace?dir=root");
  },
);
