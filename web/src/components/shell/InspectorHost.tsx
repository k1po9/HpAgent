import type { CSSProperties } from "react";
import { RunInspectorResize } from "./RunInspectorResize";
import { RunRefresh } from "../run/RunRefresh";
import { TitleCapture } from "../spatial/TitleCapture";
import { InspectorTitleOrnament } from "../spatial/InspectorTitleOrnament";
import { TaskInspector } from "../tasks/TaskInspector";
import { useEffect, useState } from "react";
import { useShell } from "../../store/shell";
import { RunInspector } from "../run/RunInspector";
import { ArtifactInspector } from "../artifact/ArtifactInspector";
import type { ArtifactSaveSource } from "../workspace/workspaceOperations";
import { useInspectorFlow, type InspectorFlow } from "./useInspectorFlow";
import { Surface } from "./Surface";
import { useMotionAllowed } from "../motion/preference";
import { flashReception } from "../spatial/receptionFeedback";
import { Maximize2, Minimize2 } from "lucide-react";
import { TaskButton } from "../ui/TaskControls";

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
export function InspectorHost(props: {
  flow?: InspectorFlow;
  compact?: boolean;
  onSaveHtml: (source: ArtifactSaveSource) => void;
  onSaveFile?: (file: { file_id: string; file_name: string }) => void;
}) {
  return props.flow ? (
    <InspectorContent {...props} flow={props.flow} />
  ) : (
    <StandaloneInspector {...props} />
  );
}
function StandaloneInspector(props: Parameters<typeof InspectorHost>[0]) {
  const compact = useCompactInspector();
  const flow = useInspectorFlow(props.compact ?? compact);
  return <InspectorContent {...props} flow={flow} />;
}
function InspectorContent({
  flow,
  onSaveHtml,
  onSaveFile,
  compact: compactOverride,
}: {
  flow: InspectorFlow;
  compact?: boolean;
  onSaveHtml: (source: ArtifactSaveSource) => void;
  onSaveFile?: (file: { file_id: string; file_name: string }) => void;
}) {
  const {
    phase,
    reveal,
    scope,
    expanded: flowExpanded,
    receiverRef,
    presentationKey,
    presentationToken,
    arrived,
    completeTitle,
  } = flow;
  const inspector = useShell((s) => s.route.inspector);
  const backStack = useShell((s) => s.backStack);
  const back = useShell((s) => s.back);
  const expanded = useShell((s) => s.expanded);
  const runWidth = useShell((s) => s.runInspectorWidth);
  const mediaCompact = useCompactInspector();
  const compact = compactOverride ?? mediaCompact;
  const tree = useWorkspace((s) => s.tree);
  const hasReceiver = inspector?.kind === "run" || inspector?.kind === "artifact";
  const objectKey = inspector ? `${inspector.kind}:${inspector.objectId}` : null;

  const allowed = useMotionAllowed();
  useEffect(() => {
    if (!arrived || !allowed) return;
    const node = document.querySelector<HTMLElement>(".hp-inspector .hp-inspector-receiver");
    if (node) return flashReception(node, () => completeTitle(presentationKey, presentationToken));
  }, [presentationKey, presentationToken, arrived, completeTitle, allowed]);
  useEffect(() => {
    if (objectKey) document.getElementById("inspector-title")?.focus({ preventScroll: true });
  }, [objectKey]);
  if (!inspector)
    return phase === "closing" ? (
      <div
        className={`hp-inspector-exit ${flow.kind === "run" ? "hp-inspector-exit--run" : ""} ${flowExpanded ? "hp-inspector-exit--expanded" : ""}`}
        aria-hidden="true"
        inert
      >
        <div ref={receiverRef} className="hp-inspector-receiver" style={flow.exitAnchor} />
      </div>
    ) : null;
  const title = !reveal
    ? "接纳中"
    : {
        run: "执行详情",
        artifact: "HTML 成果",
        file:
          tree?.nodes.find((n) => n.node_id === inspector.objectId)?.kind === "directory"
            ? "目录详情"
            : "文件详情",
        task: "任务详情",
      }[inspector.kind];
  const Reception = inspector.kind === "run" ? "span" : "header";
  const ReceptionTitle = inspector.kind === "run" ? "span" : "h3";
  const reception = (
    <Reception
      className="hp-run-identity hp-run-reception hp-capture-alt-ready"
      data-capture-kind={inspector.kind}
      aria-hidden={!reveal || undefined}
    >
      <ReceptionTitle
        ref={receiverRef}
        id={inspector.kind === "run" ? "inspector-run-title" : "inspector-artifact-title"}
        className="hp-object-kind"
        data-revealed={reveal}
      >
        {inspector.kind === "run" ? "执行详情" : "HTML 成果"}
      </ReceptionTitle>
      <TitleCapture
        key={`${presentationKey}:${presentationToken}`}
        active={reveal && flow.detailsReveal && (arrived || !allowed)}
      />
      <InspectorTitleOrnament
        key={`ornament:${presentationKey}:${presentationToken}`}
        kind={inspector.kind as "run" | "artifact"}
        active={reveal && flow.detailsReveal && (arrived || !allowed)}
      />
    </Reception>
  );
  return (
    <Surface
      title={title}
      titleContent={
        inspector.kind === "run" ? (
          <>
            {!reveal && <span className="hp-run-title-pending">接纳中</span>}
            {reception}
          </>
        ) : undefined
      }
      edgeControl={
        inspector.kind === "run" && !compact && flow.detailsReveal ? (
          <RunInspectorResize />
        ) : undefined
      }
      style={
        inspector.kind === "run"
          ? ({ "--hp-run-inspector-width": `${runWidth}px` } as CSSProperties)
          : undefined
      }
      titleRef={hasReceiver ? undefined : receiverRef}
      titleDecoration={
        (inspector.kind === "file" || inspector.kind === "task") && (
          <TitleCapture
            key={`${presentationKey}:${presentationToken}`}
            active={reveal && flow.detailsReveal && (arrived || !allowed)}
          />
        )
      }
      modal={compact}
      className={`hp-inspector hp-inspector--${inspector.kind} ${expanded ? "hp-inspector--expanded" : ""}`}
      headerActions={
        flow.detailsReveal &&
        (inspector.kind === "run" ? (
          <RunRefresh key={inspector.objectId} inspector={inspector} />
        ) : inspector.kind === "task" ? (
          <TaskButton
            type="button"
            className="hp-inspector-expand"
            aria-pressed={expanded}
            onClick={() => useShell.setState({ expanded: !expanded })}
          >
            {expanded ? (
              <Minimize2 size={16} aria-hidden="true" />
            ) : (
              <Maximize2 size={16} aria-hidden="true" />
            )}
            <span>{expanded ? "恢复宽度" : "扩大阅读"}</span>
          </TaskButton>
        ) : (
          <button
            className="hp-inspector-expand"
            aria-pressed={expanded}
            onClick={() => useShell.setState({ expanded: !expanded })}
          >
            {expanded ? (
              <Minimize2 size={16} aria-hidden="true" />
            ) : (
              <Maximize2 size={16} aria-hidden="true" />
            )}
            {expanded ? "恢复宽度" : "扩大阅读"}
          </button>
        ))
      }
      onClose={() => useShell.getState().closeInspector()}
      onBack={backStack.length ? back : undefined}
    >
      <div
        className="hp-inspector-presentation"
        data-inspector-phase={phase}
        data-inspector-arrived={flow.arrived}
        data-inspector-content={flow.detailsReveal ? "shown" : "held"}
      >
        {hasReceiver && inspector.kind !== "run" && reception}
        {!reveal && (
          <p className="hp-inspector-receiving" role="status">
            正在接纳对象…
          </p>
        )}
        <div
          hidden={!flow.detailsReveal}
          inert={!flow.detailsReveal}
          aria-hidden={!flow.detailsReveal || undefined}
          data-inspector-gate={phase}
        >
          {inspector.kind === "run" ? (
            <RunInspector
              hideIdentity
              key={`${scope}:${inspector.objectId}`}
              inspector={inspector}
              onSaveFile={onSaveFile}
            />
          ) : inspector.kind === "task" ? (
            <TaskInspector
              key={`${scope}:${inspector.objectId}`}
              inspector={inspector}
              onSaveFile={(file) => onSaveFile?.(file)}
            />
          ) : inspector.kind === "file" ? (
            <FileInspector key={`${scope}:${inspector.objectId}`} inspector={inspector} />
          ) : (
            <ArtifactInspector
              key={`${scope}:${inspector.objectId}`}
              inspector={inspector}
              onSaveHtml={onSaveHtml}
            />
          )}
        </div>
      </div>
    </Surface>
  );
}
