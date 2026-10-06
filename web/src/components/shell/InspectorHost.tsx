import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { HpApi } from "../../api/resources";
import { HpCommandError, type HpRunSnapshot, type HpWork } from "../../api/types";
import { useShell, type Inspector } from "../../store/shell";
import { useArtifacts } from "../../store/artifacts";
import { useTraceStore } from "../trace/traceStore";
import { TracePanel } from "../trace/TracePanel";
import { ArtifactPanel } from "../ArtifactPanel";
import { Surface } from "./Surface";

const resources = new HpApi(api);
function useCompactInspector() {
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 1279px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1279px)");
    const update = () => setCompact(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return compact;
}
export function InspectorHost({
  onSaveHtml,
}: {
  onSaveHtml: (html: string, name: string) => void;
}) {
  const inspector = useShell((s) => s.route.inspector);
  const backStack = useShell((s) => s.backStack);
  const back = useShell((s) => s.back);
  const expanded = useShell((s) => s.expanded);
  const compact = useCompactInspector();
  if (!inspector) return null;
  const title = { run: "执行详情", artifact: "HTML 成果", file: "文件详情", task: "任务详情" }[
    inspector.kind
  ];
  return (
    <Surface
      title={title}
      modal={compact}
      className={`hp-inspector ${expanded ? "hp-inspector--expanded" : ""}`}
      onClose={back}
      onBack={backStack.length ? back : undefined}
    >
      <button onClick={() => useShell.setState({ expanded: !expanded })}>
        {expanded ? "恢复宽度" : "扩大阅读"}
      </button>
      <InspectorBody
        key={`${inspector.kind}:${inspector.objectId}:${inspector.versionId ?? ""}`}
        inspector={inspector}
        onSaveHtml={onSaveHtml}
      />
    </Surface>
  );
}
function InspectorBody({
  inspector,
  onSaveHtml,
}: {
  inspector: Inspector;
  onSaveHtml: (html: string, name: string) => void;
}) {
  const [state, setState] = useState<"loading" | "ready" | "empty" | "unavailable" | "error">(
    "loading",
  );
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let valid = true;
    async function load() {
      try {
        if (inspector.kind === "artifact") {
          await useArtifacts
            .getState()
            .openArtifact(inspector.objectId, inspector.versionId, false);
          if (!valid) return;
          const store = useArtifacts.getState();
          if (store.error) {
            setState([403, 404].includes(store.loadErrorStatus ?? 0) ? "unavailable" : "error");
            return;
          }
          const versions = store.versionsByArtifactId[inspector.objectId] ?? [];
          if (
            inspector.versionId &&
            !versions.some((v) => v.artifact_version_id === inspector.versionId)
          ) {
            setState("unavailable");
            return;
          }
          setTitle(store.artifactsById[inspector.objectId]?.title ?? "HTML 成果");
          setState(versions.length ? "ready" : "empty");
          return;
        }
        if (inspector.kind === "run") {
          const snapshot: HpRunSnapshot = await resources.getRun(inspector.objectId);
          if (!valid) return;
          setTitle(`执行 ${snapshot.run.run_id}`);
          setSummary(snapshot.run.status);
          useTraceStore.getState().selectRun(inspector.objectId);
        } else if (inspector.kind === "task") {
          const { work } = await api.request<{ work: HpWork }>({
            method: "GET",
            path: `/api/v1/works/${encodeURIComponent(inspector.objectId)}`,
          });
          if (!valid) return;
          setTitle(work.title);
          setSummary(`${work.requirement.objective} · ${work.status}`);
        } else {
          const tree = await resources.getWorkspace();
          if (!valid) return;
          const node = tree.nodes.find((n) => n.node_id === inspector.objectId);
          if (!node) {
            setState("unavailable");
            return;
          }
          setTitle(node.name);
          setSummary(node.kind === "directory" ? "目录" : "文件");
        }
        setState("ready");
      } catch (error) {
        if (valid)
          setState(
            error instanceof HpCommandError && [403, 404].includes(error.status)
              ? "unavailable"
              : "error",
          );
      }
    }
    void load();
    return () => {
      valid = false;
      if (inspector.kind === "run") useTraceStore.getState().reset();
    };
  }, [inspector, attempt]);
  if (state === "loading") return <p role="status">正在加载对象…</p>;
  if (state === "unavailable")
    return (
      <div>
        <p role="alert">对象不可用。</p>
        <button onClick={() => useShell.getState().closeInspector()}>回到所属页面</button>
      </div>
    );
  if (state === "error")
    return (
      <div>
        <p role="alert">暂时无法同步对象。</p>
        <button
          onClick={() => {
            setState("loading");
            setAttempt(attempt + 1);
          }}
        >
          重试
        </button>
      </div>
    );
  if (state === "empty") return <p>暂无对象内容。</p>;
  return (
    <>
      <h3>{title}</h3>
      {summary && <p>{summary}</p>}
      {inspector.kind === "artifact" && (
        <ArtifactPanel
          embedded
          selectedArtifactId={inspector.objectId}
          selectedVersionId={inspector.versionId}
          onSaveHtml={onSaveHtml}
        />
      )}
      {inspector.kind === "run" && <TracePanel embedded />}
      {(inspector.kind === "file" || inspector.kind === "task") && (
        <button
          onClick={() =>
            useShell
              .getState()
              .navigate({ screen: inspector.kind === "file" ? "workspace" : "tasks" })
          }
        >
          在所属页面管理
        </button>
      )}
    </>
  );
}
