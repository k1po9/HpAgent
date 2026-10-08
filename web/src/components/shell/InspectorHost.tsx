import { TaskInspector } from "../tasks/TaskInspector";
import { useEffect, useState } from "react";
import { useShell } from "../../store/shell";
import { RunInspector } from "../run/RunInspector";
import { ArtifactInspector } from "../artifact/ArtifactInspector";
import type { ArtifactSaveSource } from "../workspace/workspaceOperations";
import { Surface } from "./Surface";

import { useWorkspace } from "../../store/workspace";
import { FileInspector } from "../workspace/FileInspector";
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
  onSaveFile,
}: {
  onSaveHtml: (source: ArtifactSaveSource) => void;
  onSaveFile?: (file: { file_id: string; file_name: string }) => void;
}) {
  const inspector = useShell((s) => s.route.inspector);
  const backStack = useShell((s) => s.backStack);
  const back = useShell((s) => s.back);
  const expanded = useShell((s) => s.expanded);
  const compact = useCompactInspector();
  const tree = useWorkspace((s) => s.tree);
  const objectKey = inspector ? `${inspector.kind}:${inspector.objectId}` : null;
  useEffect(() => {
    if (objectKey) document.getElementById("inspector-title")?.focus();
  }, [objectKey]);
  if (!inspector) return null;
  const title = {
    run: "执行详情",
    artifact: "HTML 成果",
    file:
      tree?.nodes.find((n) => n.node_id === inspector.objectId)?.kind === "directory"
        ? "目录详情"
        : "文件详情",
    task: "任务详情",
  }[inspector.kind];
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
      {inspector.kind === "run" ? (
        <RunInspector key={inspector.objectId} inspector={inspector} onSaveFile={onSaveFile} />
      ) : inspector.kind === "task" ? (
        <TaskInspector
          key={inspector.objectId}
          inspector={inspector}
          onSaveFile={(file) => onSaveFile?.(file)}
        />
      ) : inspector.kind === "file" ? (
        <FileInspector key={inspector.objectId} inspector={inspector} />
      ) : (
        <ArtifactInspector key={inspector.objectId} inspector={inspector} onSaveHtml={onSaveHtml} />
      )}
    </Surface>
  );
}
