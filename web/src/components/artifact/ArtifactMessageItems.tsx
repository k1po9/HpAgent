import { Button } from "../ui";
import { useReadingPresentation } from "../mercury/readingPresentation";
import { FileCode, ChevronRight } from "lucide-react";
import { useEffect, useRef } from "react";
import { useArtifacts } from "../../store/artifacts";
import { useShell } from "../../store/shell";
import { artifactStatus } from "./artifactPresentation";
export function ArtifactMessageItems({
  messageId,
  discoverOnEntry = false,
}: {
  messageId: string;
  discoverOnEntry?: boolean;
}) {
  const { retiring } = useReadingPresentation();
  const root = useRef<HTMLDivElement>(null);
  const items = useArtifacts((s) => s.artifactsByMessageId[messageId]);
  const query = useArtifacts((s) => s.messageQueries[messageId]);
  const intent = useArtifacts((s) => s.intents[`message:${messageId}`]);
  useEffect(() => {
    if (retiring) return;
    const node = root.current;
    if (!node) return;
    // Observe the message, not its footer: a long answer can be visible while
    // its HTML controls are still outside the viewport.
    const target = node.closest<HTMLElement>("[data-message-id]") ?? node;
    const viewport = node.closest<HTMLElement>(".hp-thread__viewport");
    let dispatched = false;
    const load = () => {
      if (dispatched || node.closest("[data-mercury-retiring], [hidden], [inert]")) return;
      const rect = target.getBoundingClientRect();
      const bounds = viewport?.getBoundingClientRect();
      if (!discoverOnEntry && bounds && (rect.bottom <= bounds.top || rect.top >= bounds.bottom))
        return;
      dispatched = true;
      void useArtifacts.getState().loadForMessage(messageId);
    };
    const observer =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) load();
          });
    observer?.observe(target);
    // Shell/reading transitions can release inert without another intersection
    // event. Recheck on release and on scrolling, with one bounded entry read.
    const presentation = new MutationObserver(load);
    for (let parent: HTMLElement | null = node; parent; parent = parent.parentElement)
      presentation.observe(parent, {
        attributes: true,
        attributeFilter: ["hidden", "inert", "data-mercury-retiring"],
      });
    viewport?.addEventListener("scroll", load, { passive: true });
    load();
    return () => {
      observer?.disconnect();
      presentation.disconnect();
      viewport?.removeEventListener("scroll", load);
    };
  }, [messageId, retiring, discoverOnEntry]);
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
        <Button
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
            <span className="hp-object-kind">HTML 成果</span>
            <strong>{item.artifact.title}</strong>
            <small className="hp-artifact-message-meta">
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
        </Button>
      ))}
      {query?.error ? (
        <p role="alert">
          {query.error}{" "}
          <Button
            type="button"
            onClick={() => useArtifacts.getState().loadForMessage(messageId, true)}
          >
            重试成果查询
          </Button>
        </p>
      ) : (
        <Button
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
        </Button>
      )}
      {intent?.result?.error && <p role="alert">{intent.result.error}</p>}
      {intent?.uncertain && <p>结果尚未确认，恢复会重放同一个生成请求。</p>}
    </div>
  );
}
