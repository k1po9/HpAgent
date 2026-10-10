import { FileCode, ChevronRight } from "lucide-react";
import { useEffect, useRef } from "react";
import { useArtifacts } from "../../store/artifacts";
import { useShell } from "../../store/shell";
import { artifactStatus } from "./artifactPresentation";
export function ArtifactMessageItems({ messageId }: { messageId: string }) {
  const root = useRef<HTMLDivElement>(null);
  const items = useArtifacts((s) => s.artifactsByMessageId[messageId]);
  const query = useArtifacts((s) => s.messageQueries[messageId]);
  const intent = useArtifacts((s) => s.intents[`message:${messageId}`]);
  useEffect(() => {
    const load = () => {
      void useArtifacts.getState().loadForMessage(messageId);
    };
    if (typeof IntersectionObserver === "undefined") {
      load();
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        load();
        observer.disconnect();
      }
    });
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, [messageId]);
  const create = async () => {
    const shell = useShell.getState(),
      token = shell.requestToken;
    const result = await useArtifacts.getState().createArtifact(messageId);
    if (
      root.current?.isConnected &&
      result.status === "success" &&
      result.artifact &&
      useShell.getState().requestToken === token
    )
      useShell.getState().openInspector({
        kind: "artifact",
        objectId: result.artifact.artifact_id,
        versionId: result.version?.artifact_version_id,
        origin: {
          conversationId: shell.route.conversationId,
          messageId,
          triggerId: `artifact-create-${messageId}`,
        },
      });
  };
  return (
    <div ref={root} className="hp-artifact-message-items" aria-label="消息 HTML 成果">
      {items?.map((item) => (
        <button
          type="button"
          key={item.artifact.artifact_id}
          className="hp-artifact-message-card"
          data-status={item.latest_version?.status}
          aria-label={`${item.artifact.title} · HTML · ${item.latest_version ? `v${item.latest_version.version} ${artifactStatus[item.latest_version.status]}` : "暂无版本"}`}
          id={`artifact-open-${item.artifact.artifact_id}`}
          onClick={() =>
            useShell.getState().openInspector({
              kind: "artifact",
              objectId: item.artifact.artifact_id,
              origin: {
                conversationId: item.artifact.conversation_id ?? undefined,
                messageId,
                triggerId: `artifact-open-${item.artifact.artifact_id}`,
              },
            })
          }
        >
          <FileCode className="hp-artifact-message-icon" size={24} aria-hidden="true" />
          <span className="hp-artifact-message-copy">
            <strong>{item.artifact.title}</strong>
            <small className="hp-artifact-message-meta">
              <span className="hp-artifact-kind">HTML</span>
              <span>
                {item.latest_version
                  ? `v${item.latest_version.version} ${artifactStatus[item.latest_version.status]}`
                  : "暂无版本"}
              </span>
            </small>
          </span>
          <span className="hp-artifact-message-open" aria-hidden="true">
            <span>打开</span>
            <ChevronRight size={16} />
          </span>
        </button>
      ))}
      {query?.error ? (
        <p role="alert">
          {query.error}{" "}
          <button
            type="button"
            onClick={() => useArtifacts.getState().loadForMessage(messageId, true)}
          >
            重试成果查询
          </button>
        </p>
      ) : (
        <button
          id={`artifact-create-${messageId}`}
          type="button"
          disabled={!items || query?.loading || intent?.busy}
          onClick={() => void create()}
        >
          {intent?.busy
            ? "提交中…"
            : intent?.uncertain
              ? "恢复原 HTML 生成"
              : items?.length
                ? "再生成一个 HTML"
                : "生成 HTML"}
        </button>
      )}
      {intent?.result?.error && <p role="alert">{intent.result.error}</p>}
      {intent?.uncertain && <p>结果尚未确认，恢复会重放同一个生成请求。</p>}
    </div>
  );
}
