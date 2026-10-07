import { useState } from "react";
import { api } from "../../api/client";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useWorks } from "../../store/works";
import { commandError } from "../../utils/commands";
import { useWorkspaceQuery } from "../workspace/useWorkspaceQuery";
import { workCommand, permissionsChanged, type Subject } from "../workspace/workspaceOperations";
import { validSnapshot } from "./taskActions";
export function WorkInputs({ subject, fileId }: { subject: Subject; fileId: string | null }) {
  const result = useWorkspaceQuery(`inputs:${subject.id}`, () =>
    api.request<{ items: Array<{ ref_id: string; file_id: string; purpose: string }> }>({
      method: "GET",
      path: `/api/v1/works/${encodeURIComponent(subject.id)}/inputs`,
    }),
  );
  const work = useWorks((s) => s.items.find((w) => w.work_id === subject.id));
  const metadata = useWorkspaceQuery(fileId ? `file:${fileId}` : null, () =>
    workspaceApi.getFile(fileId!),
  );
  const canAdd =
    metadata.data?.file.status === "ready" &&
    metadata.data.file.purpose === "input" &&
    Boolean(work && validSnapshot(work) && !["completed", "stopped"].includes(work.status));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useWorkspace((s) => s.generation);
  function submit(suffix: string, body: unknown, method: "POST" | "DELETE" = "POST") {
    if (busy) return;
    setBusy(true);
    setError("");
    void workCommand(subject.id, suffix, body, method)
      .then(() => {
        if (generation !== useWorkspace.getState().generation) return;
        permissionsChanged();
        setNotice(
          method === "DELETE"
            ? "输入引用已撤销，当前相关执行可能停止，请查看最新状态。"
            : "工作输入引用已更新，下一轮生效。",
        );
      })
      .catch((e) => {
        if (generation === useWorkspace.getState().generation) setError(commandError(e));
      })
      .finally(() => {
        if (generation === useWorkspace.getState().generation) setBusy(false);
      });
  }
  return (
    <section>
      <h3>输入文件引用</h3>
      <p>输入引用与目录授权分别管理。</p>
      <button
        disabled={busy || !fileId || !canAdd}
        onClick={() => submit("inputs", { file_id: fileId, purpose: "input" })}
      >
        作为工作输入文件
      </button>
      {result.data?.items.map((ref) => (
        <p key={ref.ref_id}>
          {ref.file_id}
          <button
            disabled={busy}
            onClick={() => submit(`inputs/${encodeURIComponent(ref.ref_id)}`, undefined, "DELETE")}
          >
            撤销输入
          </button>
        </p>
      ))}
      {result.error && (
        <p role="alert">
          输入查询失败。<button onClick={result.retry}>重试输入</button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
