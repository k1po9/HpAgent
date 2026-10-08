import { useRef } from "react";
import { useArtifacts, building, latestSuccess } from "../../store/artifacts";
import { useArtifactUi } from "../../store/artifactUi";
import type { HpArtifactVersion } from "../../api/types";
export function ArtifactComposer({
  artifactId,
  versions,
  onSelect,
}: {
  artifactId: string;
  versions: HpArtifactVersion[];
  onSelect: (id: string) => void;
}) {
  const draft = useArtifactUi((s) => s.drafts[artifactId]);
  const intent = useArtifacts((s) => s.intents[`version:${artifactId}`]);
  const composing = useRef(false);
  const parent = latestSuccess(versions),
    running = versions.find(building);
  const text = draft?.text ?? "",
    count = [...text.trim()].length;
  return (
    <form
      className="hp-artifact-composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (composing.current || intent?.busy) return;
        const revision = draft?.revision ?? 0;
        void useArtifacts
          .getState()
          .createVersion(artifactId, text, parent?.artifact_version_id ?? null, revision)
          .then((result) => {
            if (result.status === "success")
              useArtifactUi.getState().clearSubmitted(artifactId, result.draftRevision ?? revision);
          });
      }}
    >
      <p>
        {parent ? `基于最近成功版本 v${parent.version} 修改` : "尚无成功版本可继承，将重新生成。"}{" "}
        {parent && (
          <button type="button" onClick={() => onSelect(parent.artifact_version_id)}>
            查看修改基准
          </button>
        )}
      </p>
      <label htmlFor={`artifact-instruction-${artifactId}`}>修改指令</label>
      <textarea
        id={`artifact-instruction-${artifactId}`}
        value={text}
        onChange={(e) => useArtifactUi.getState().edit(artifactId, e.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        aria-describedby={`artifact-feedback-${artifactId}`}
        placeholder="描述希望如何修改 HTML…"
      />
      <p id={`artifact-feedback-${artifactId}`} role="status">
        {count}/4000 个字符{count > 4000 ? "，请缩短指令。" : ""}
        {running ? ` · v${running.version} 正在构建` : ""}
      </p>
      {intent?.result?.error && <p role="alert">{intent.result.error}</p>}
      {intent?.result?.status === "success" && (
        <p role="status">已提交 v{intent.result.version?.version}，可在版本历史查看。</p>
      )}
      {intent?.uncertain && <p>提交结果尚未确认；恢复会使用原指令和同一请求标识。</p>}
      <button
        disabled={
          intent?.busy || (!intent?.uncertain && (Boolean(running) || !count || count > 4000))
        }
      >
        {intent?.busy ? "提交中…" : intent?.uncertain ? "恢复原修改" : "生成新版本"}
      </button>
    </form>
  );
}
