// Temporary UI-7 adapter for the legacy development page. Shell owns selection.
import { useShell } from "../store/shell";
import { ArtifactInspector } from "./artifact/ArtifactInspector";
import type { ArtifactSaveSource } from "./workspace/workspaceOperations";
export function ArtifactPanel({
  onSaveHtml,
}: { onSaveHtml?: (source: ArtifactSaveSource) => void } = {}) {
  const inspector = useShell((s) => s.route.inspector);
  return inspector?.kind === "artifact" ? (
    <ArtifactInspector inspector={inspector} onSaveHtml={onSaveHtml} />
  ) : null;
}
