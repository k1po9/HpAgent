import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { HpApi } from "../api/resources";
import type { HpRunSnapshot } from "../api/types";
import { useWorkbench } from "../store/workbench";
import { useWorks } from "../store/works";
import { useArtifacts } from "../store/artifacts";
import { ArtifactPanel } from "./ArtifactPanel";
import { TracePanel } from "./trace/TracePanel";
import { useTraceStore } from "./trace/traceStore";
import { newIdempotencyKey } from "../utils/idempotency";
import { commandError, useCommandKey } from "../utils/commands";

type SaveSource = { file_id: string; file_name: string } | { html: string; file_name: string };
const resources = new HpApi(api);
export function SaveWorkspaceDialog({
  file,
  onClose,
  onSaved,
}: {
  file: SaveSource;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [tree, setTree] = useState<Awaited<ReturnType<HpApi["getWorkspace"]>> | null>(null);
  const [directory, setDirectory] = useState("");
  const [name, setName] = useState(file.file_name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const keyFor = useCommandKey();
  const [uploadKey] = useState(newIdempotencyKey);
  const uploaded = useRef<string | null>(null);
  useEffect(() => {
    let valid = true;
    void resources
      .getWorkspace()
      .then((t) => {
        if (valid) {
          setTree(t);
          setDirectory(t.root_id);
        }
      })
      .catch((e) => {
        if (valid) setError(commandError(e));
      });
    return () => {
      valid = false;
    };
  }, []);
  function path(id: string): string {
    const n = tree?.nodes.find((item) => item.node_id === id);
    return n ? (n.parent_id ? `${path(n.parent_id).replace(/\/$/, "")}/${n.name}` : "/") : "";
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      if ("html" in file && !uploaded.current) {
        const output = new File([file.html], file.file_name, { type: "text/plain" });
        const upload = await resources.createWorkspaceUpload(output, uploadKey);
        const ready = await resources.uploadContent(upload.content_url, output);
        uploaded.current = ready.file_id;
      }
      const fileId = "file_id" in file ? file.file_id : uploaded.current!;
      await resources.saveWorkspaceFile(
        directory,
        fileId,
        name,
        keyFor({ directory, name, file: fileId }),
      );
      onSaved();
      onClose();
    } catch (e) {
      setError(commandError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="hp-dialog-backdrop">
      <form
        role="dialog"
        aria-modal="true"
        aria-label="保存到长期文件"
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <h2>保存到长期文件</h2>
        <p>源文件：{file.file_name}</p>
        {"html" in file && <p>以文本保存 HTML 源码；交互预览仍在成果页面。</p>}
        <label>
          保存目录
          <select value={directory} onChange={(e) => setDirectory(e.target.value)}>
            {tree?.nodes
              .filter((n) => n.kind === "directory")
              .map((n) => (
                <option key={n.node_id} value={n.node_id}>
                  {path(n.node_id) || "/"}
                </option>
              ))}
          </select>
        </label>
        <label>
          保存名称
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button disabled={busy || !directory}>{busy ? "保存中…" : "确认保存"}</button>{" "}
        <button type="button" disabled={busy} onClick={onClose}>
          取消
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
    </div>
  );
}

export function ArtifactsPage({ onWorkspaceSaved }: { onWorkspaceSaved?: () => void }) {
  const [saveSource, setSaveSource] = useState<SaveSource | null>(null);
  const messages = useWorkbench((s) => s.messages);
  const byMessage = useArtifacts((s) => s.artifactsByMessageId);
  const create = useArtifacts((s) => s.createArtifact);
  const load = useArtifacts((s) => s.loadForMessage);
  const open = useArtifacts((s) => s.openArtifact);
  const error = useArtifacts((s) => s.error);
  const [messageId, setMessageId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const candidates = messages.filter(
    (m) => m.role === "assistant" && m.status === "completed" && m.content,
  );
  const selected = candidates.some((m) => m.message_id === messageId) ? messageId : "";
  return (
    <>
      <form
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          void create(selected, instruction || null).finally(() => setBusy(false));
        }}
      >
        <h2>成果与版本</h2>
        <label>
          来源消息
          <select
            required
            value={selected}
            onChange={(e) => {
              setMessageId(e.target.value);
              if (e.target.value) void load(e.target.value);
            }}
          >
            <option value="">选择当前对话已完成的回复</option>
            {candidates.map((m) => (
              <option key={m.message_id} value={m.message_id}>
                {m.sequence} · {m.content?.slice(0, 80)}
              </option>
            ))}
          </select>
        </label>
        <label>
          生成要求
          <textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} />
        </label>
        <button disabled={busy || !selected}>{busy ? "创建中…" : "创建成果"}</button>
        {(byMessage[selected] ?? []).map((a) => (
          <button
            type="button"
            key={a.artifact.artifact_id}
            onClick={() => void open(a.artifact.artifact_id)}
          >
            {a.artifact.title}
          </button>
        ))}
        {error && <p role="alert">{error}</p>}
        {!candidates.length && <p>先在对话页完成一次回复，或从工作列表打开已有成果。</p>}
      </form>
      <ArtifactPanel
        onSaveHtml={(html, name) => setSaveSource({ html, file_name: `${name}.txt` })}
      />
      {saveSource && (
        <SaveWorkspaceDialog
          file={saveSource}
          onClose={() => setSaveSource(null)}
          onSaved={() => onWorkspaceSaved?.()}
        />
      )}
    </>
  );
}

export function DiagnosticsPage() {
  const messages = useWorkbench((s) => s.messages);
  const activeRun = useWorkbench((s) => s.activeRun);
  const works = useWorks((s) => s.items);
  const traceRunId = useTraceStore((s) => s.runId);
  const [workId, setWorkId] = useState("");
  const [workRuns, setWorkRuns] = useState<Array<{ run_id: string; status: string }>>([]);
  const [snapshot, setSnapshot] = useState<HpRunSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ids = [
    ...new Set([
      ...(activeRun ? [activeRun.run_id] : []),
      ...messages.flatMap((m) => (m.produced_by_run_id ? [m.produced_by_run_id] : [])),
    ]),
  ];
  useEffect(() => {
    let valid = true;
    if (workId)
      void resources
        .listResearchRuns(workId)
        .then((page) => {
          if (valid) setWorkRuns(page.items);
        })
        .catch((e) => {
          if (valid) setError(commandError(e));
        });
    return () => {
      valid = false;
    };
  }, [workId]);
  useEffect(() => {
    let valid = true;
    if (traceRunId)
      void resources
        .getRun(traceRunId)
        .then((run) => {
          if (valid) setSnapshot(run);
        })
        .catch((e) => {
          if (valid) setError(commandError(e));
        });
    return () => {
      valid = false;
    };
  }, [traceRunId]);
  function select(id: string) {
    setSnapshot(null);
    setError(null);
    useTraceStore.getState().followRun(id || null);
    useTraceStore.getState().setOpen(Boolean(id));
  }
  return (
    <>
      <section className="hp-operation-form">
        <h2>执行诊断</h2>
        <label>
          对话执行
          <select
            value={ids.includes(traceRunId ?? "") ? (traceRunId ?? "") : ""}
            onChange={(e) => select(e.target.value)}
          >
            <option value="">选择执行</option>
            {ids.map((id, i) => (
              <option key={id} value={id}>
                第 {ids.length - i} 次 · {id.slice(0, 8)}
                {id === activeRun?.run_id ? ` · ${activeRun.status}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          工作
          <select
            value={workId}
            onChange={(e) => {
              setWorkId(e.target.value);
              setWorkRuns([]);
              setError(null);
            }}
          >
            <option value="">选择工作</option>
            {works.map((w) => (
              <option key={w.work_id} value={w.work_id}>
                {w.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          工作执行
          <select
            value={workRuns.some((r) => r.run_id === traceRunId) ? (traceRunId ?? "") : ""}
            onChange={(e) => select(e.target.value)}
          >
            <option value="">选择执行</option>
            {workRuns.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.status} · {r.run_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        {snapshot && (
          <div>
            <p>
              执行：{snapshot.run.run_id} · {snapshot.run.status}
            </p>
            {snapshot.source_kind === "chat" && snapshot.run.failure && (
              <p role="alert">
                {snapshot.run.failure.message} · {snapshot.run.failure.code} ·{" "}
                {snapshot.run.failure.retryable ? "可重试" : "需处理原因后重新发起"}
              </p>
            )}
          </div>
        )}
        <details>
          <summary>按执行编号查询</summary>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const id = new FormData(e.currentTarget).get("runId");
              if (typeof id === "string") select(id.trim());
            }}
          >
            <input name="runId" required aria-label="执行编号" />
            <button>查询</button>
          </form>
        </details>
        {error && <p role="alert">{error}</p>}
      </section>
      <TracePanel />
    </>
  );
}
