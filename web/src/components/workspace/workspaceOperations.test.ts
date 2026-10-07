import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useWorks } from "../../store/works";
import { useWorkbench } from "../../store/workbench";
import { api } from "../../api/client";
import { workspaceApi, useWorkspace } from "../../store/workspace";
import {
  resetWorkspaceOperations,
  resumeSave,
  startSave,
  useWorkspaceOperations,
  grantMissing,
  workCommand,
  revokeRules,
  observeRevocation,
  useAffectedRuns,
} from "./workspaceOperations";
import { useTraceStore } from "../trace/traceStore";
import type { HpFile } from "../../api/types";
const ready: HpFile = {
  file_id: "file",
  file_name: "notes.txt",
  purpose: "input",
  status: "ready",
  size_bytes: 5,
  content_type: "text/plain",
  encoding: "utf-8",
  sha256: "hash",
  failure_code: null,
  download_url: null,
};
afterEach(() => vi.useRealTimers());
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(useWorks.getState(), "load").mockResolvedValue();
  vi.spyOn(useWorkbench.getState(), "refreshActiveRun").mockResolvedValue();
  useWorkspace.getState().reset();
  resetWorkspaceOperations();
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "root",
    nodes: [],
  });
  vi.spyOn(workspaceApi, "createWorkspaceUpload").mockResolvedValue({
    file: { ...ready, status: "uploading" },
    content_url: "/put",
  });
  vi.spyOn(workspaceApi, "getFile").mockResolvedValue({ file: { ...ready, status: "uploading" } });
  vi.spyOn(workspaceApi, "uploadContent").mockResolvedValue(ready);
  vi.spyOn(workspaceApi, "saveWorkspaceFile").mockResolvedValue({ node_id: "node" });
  vi.spyOn(workspaceApi, "listConversationResources").mockResolvedValue({
    grants: [],
    attachments: [],
  });
  vi.spyOn(workspaceApi, "grantConversationResource").mockResolvedValue({ grant_ids: [] });
});
it("saves by default without granting an active conversation", async () => {
  const id = startSave(
    new File(["hello"], "notes.txt", { type: "text/plain" }),
    "root",
    "notes.txt",
  );
  await resumeSave(id);
  expect(workspaceApi.grantConversationResource).not.toHaveBeenCalled();
  expect(useWorkspaceOperations.getState().operations[id]?.completed).toBe(true);
});
it("keeps the initialized file and upload key after an unknown content response", async () => {
  vi.mocked(workspaceApi.uploadContent).mockRejectedValueOnce(new Error("response lost"));
  const id = startSave(new File(["hello"], "notes.txt"), "root", "notes.txt");
  await resumeSave(id);
  vi.mocked(workspaceApi.getFile).mockResolvedValue({ file: ready });
  await resumeSave(id);
  expect(workspaceApi.createWorkspaceUpload).toHaveBeenCalledTimes(1);
  expect(workspaceApi.uploadContent).toHaveBeenCalledTimes(1);
  expect(workspaceApi.saveWorkspaceFile).toHaveBeenCalledTimes(1);
});
it("replays an unknown save with the same key and payload, without transferring content again", async () => {
  vi.mocked(workspaceApi.saveWorkspaceFile).mockRejectedValueOnce(new Error("response lost"));
  const id = startSave(new File(["hello"], "notes.txt"), "directory-A", "fixed-name.txt");
  await resumeSave(id);
  await resumeSave(id);
  expect(workspaceApi.uploadContent).toHaveBeenCalledTimes(1);
  expect(vi.mocked(workspaceApi.saveWorkspaceFile).mock.calls[0]).toEqual(
    vi.mocked(workspaceApi.saveWorkspaceFile).mock.calls[1],
  );
});
it("recovers authorization alone after save success and only grants missing rules", async () => {
  vi.mocked(workspaceApi.grantConversationResource).mockRejectedValueOnce(new Error("offline"));
  const subject = { kind: "conversation" as const, id: "c1", title: "A" };
  const id = startSave(new File(["hello"], "notes.txt"), "root", "notes.txt", subject);
  await resumeSave(id);
  vi.mocked(workspaceApi.listConversationResources).mockResolvedValue({
    attachments: [],
    grants: [
      {
        grant_id: "g1",
        node_id: "node",
        name: "notes",
        kind: "file",
        operation: "list_metadata",
        recursive: false,
      },
    ],
  });
  await resumeSave(id);
  expect(workspaceApi.saveWorkspaceFile).toHaveBeenCalledTimes(1);
  expect(workspaceApi.uploadContent).toHaveBeenCalledTimes(1);
  expect(workspaceApi.grantConversationResource).toHaveBeenLastCalledWith(
    "c1",
    "node",
    ["read_content"],
    false,
  );
});
it("deduplicates double clicks and rejects late results after reset", async () => {
  let resolve!: (v: { node_id: string }) => void;
  vi.mocked(workspaceApi.saveWorkspaceFile).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const id = startSave({ file_id: "file", file_name: "out.txt" }, "root", "out.txt");
  const pending = resumeSave(id);
  await resumeSave(id);
  expect(workspaceApi.saveWorkspaceFile).toHaveBeenCalledTimes(1);
  useWorkspace.getState().reset();
  resetWorkspaceOperations();
  resolve({ node_id: "late" });
  await pending;
  expect(useWorkspaceOperations.getState().operations).toEqual({});
});
it("serializes Work grants and preserves If-Match and command key on uncertain retry", async () => {
  const request = vi
    .spyOn(api, "request")
    .mockResolvedValueOnce({ work: { row_version: 7 } })
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ work: { row_version: 8 } });
  await expect(workCommand("w1", "resources", { node_id: "n" })).rejects.toThrow();
  await workCommand("w1", "resources", { node_id: "n" });
  expect(request.mock.calls[1]?.[0]).toEqual(request.mock.calls[2]?.[0]);
  expect(request.mock.calls[1]?.[0].headers).toEqual({ "If-Match": '"work-w1-v7"' });
});
it("does not use the Conversation contract for Work grants", async () => {
  const request = vi
    .spyOn(api, "request")
    .mockImplementation(async (init) =>
      init.method === "GET" && init.path.endsWith("resources")
        ? { grants: [] }
        : { work: { row_version: 4 } },
    );
  await grantMissing({ kind: "work", id: "w1", title: "task" }, "n", ["read_content"], false);
  expect(workspaceApi.grantConversationResource).not.toHaveBeenCalled();
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/api/v1/works/w1/resources",
      idempotencyKey: expect.any(String),
      headers: { "If-Match": '"work-w1-v4"' },
    }),
  );
});
it("uses read-back after DELETE response loss to determine remaining rules", async () => {
  const grant = {
    grant_id: "g",
    node_id: "n",
    name: "file",
    kind: "file" as const,
    operation: "read_content" as const,
    recursive: false,
  };
  vi.mocked(workspaceApi.listConversationResources)
    .mockResolvedValueOnce({ grants: [grant], attachments: [] })
    .mockResolvedValueOnce({ grants: [], attachments: [] });
  vi.spyOn(workspaceApi, "revokeConversationResource").mockRejectedValue(
    new Error("response lost"),
  );
  expect(await revokeRules({ kind: "conversation", id: "c", title: "A" }, [grant])).toEqual([]);
});

it("deduplicates in-flight revocation checks and waits for a terminal snapshot", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  let resolve!: (value: never) => void;
  const getRun = vi
    .spyOn(workspaceApi, "getRun")
    .mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    )
    .mockResolvedValue({ run: { status: "cancelled" } } as never);
  const clearInputs = vi.spyOn(useTraceStore.getState(), "clearModelInputs");
  observeRevocation(["r1", "r1"]);
  observeRevocation(["r1"]);
  expect(getRun).toHaveBeenCalledTimes(1);
  expect(clearInputs).toHaveBeenCalled();
  expect(useAffectedRuns.getState().states.r1).toContain("等待执行端确认");
  resolve({ run: { status: "running" } } as never);
  await vi.advanceTimersByTimeAsync(0);
  expect(useAffectedRuns.getState().states.r1).toContain("等待执行端确认");
  await vi.advanceTimersByTimeAsync(2000);
  expect(getRun).toHaveBeenCalledTimes(2);
  expect(useAffectedRuns.getState().states.r1).toBe("执行已结束（cancelled）");
  expect(useWorkbench.getState().refreshActiveRun).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(10000);
  expect(getRun).toHaveBeenCalledTimes(2);
});

it("pauses hidden checks, bounds concurrency and discards old-account snapshots", async () => {
  vi.useFakeTimers();
  let hidden = true;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  const done: Array<(value: never) => void> = [];
  const getRun = vi
    .spyOn(workspaceApi, "getRun")
    .mockImplementation(() => new Promise((resolve) => done.push(resolve)));
  observeRevocation(["old1", "old2", "old3", "old4", "old5"]);
  expect(getRun).not.toHaveBeenCalled();
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(getRun).toHaveBeenCalledTimes(3);
  useWorkspace.getState().reset();
  resetWorkspaceOperations();
  observeRevocation(["new"]);
  expect(getRun).toHaveBeenCalledTimes(4);
  done.slice(0, 3).forEach((resolve) => resolve({ run: { status: "cancelled" } } as never));
  await vi.advanceTimersByTimeAsync(0);
  observeRevocation(["new"]);
  expect(getRun).toHaveBeenCalledTimes(4);
  expect(Object.keys(useAffectedRuns.getState().states)).toEqual(["new"]);
  done[3]!({ run: { status: "cancelled" } } as never);
  await vi.advanceTimersByTimeAsync(10000);
  expect(getRun).toHaveBeenCalledTimes(4);
  expect(useAffectedRuns.getState().states.new).toBe("执行已结束（cancelled）");
});
