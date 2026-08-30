/**
 * assistant-ui chat surface (contract §14.1).
 *
 * The single place that composes assistant-ui's primitives into the chat UI.
 * Everything else in the app treats this as a black box that renders the Hp
 * message history and reports user intent through `onSend` / `onCancel`.
 *
 * regenerate/branch are not surfaced (no onReload/onEdit wiring): a failed or
 * cancelled Run is retried from the run-status area (phase-e E-04).
 */
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { Flex, Spinner, Text } from "@radix-ui/themes";
import { FileText, Paperclip, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { HpFile, HpMessage, HpRun } from "../../api/types";
import { useHpThreadRuntime } from "./runtime";
import { useArtifacts } from "../../store/artifacts";
import type { UploadAttachment } from "../../store/workbench";

export interface HpThreadProps {
  messages: HpMessage[];
  activeRun: HpRun | null;
  attachments: UploadAttachment[];
  fileUploadEnabled: boolean;
  sendDisabled: boolean;
  onFilesSelected: (files: File[]) => void;
  onRemoveAttachment: (localId: string) => void;
  onSend: (content: string) => boolean | Promise<boolean>;
  onCancel: () => void;
}

/**
 * Text part renderer: assistant replies carry Markdown (fenced code, lists,
 * emphasis) and must render as such. react-markdown emits no raw HTML by
 * default, so the text stays inert.
 */
function HpTextPart({ text }: { text: string }) {
  return (
    <div className="hp-text-part">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function HpMessageView({ filesByMessageId }: { filesByMessageId: Record<string, HpFile[]> }) {
  const message = useAuiState((s) => s.message);
  const files = filesByMessageId[message.id] ?? [];
  const artifacts = useArtifacts((s) => s.artifactsByMessageId[message.id]);
  const loading = useArtifacts((s) => s.loadingMessageIds.includes(message.id));
  const loadForMessage = useArtifacts((s) => s.loadForMessage);
  const createArtifact = useArtifacts((s) => s.createArtifact);
  const openArtifact = useArtifacts((s) => s.openArtifact);
  const canBuild = message.role === "assistant" && message.status?.type === "complete";

  const primaryAction = async () => {
    const known = artifacts ?? (await loadForMessage(message.id));
    // A reset invalidates an in-flight lookup. Do not reinterpret that stale
    // response (or a lookup error) as "no Artifact" and create one implicitly.
    if (known === null) return;
    if (known[0]) await openArtifact(known[known.length - 1]!.artifact.artifact_id);
    else await createArtifact(message.id);
  };
  return (
    <MessagePrimitive.Root className="hp-msg">
      <MessagePrimitive.If user>
        <span className="hp-msg__marker hp-msg__marker--user" aria-hidden="true" />
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <span className="hp-msg__marker hp-msg__marker--assistant" aria-hidden="true" />
      </MessagePrimitive.If>
      <MessagePrimitive.Parts components={{ Text: HpTextPart }} />
      {files.length ? (
        <div className="hp-msg__files" aria-label="消息附件">
          {files.map((file) => (
            <a
              className="hp-msg__file"
              href={file.download_url ?? undefined}
              aria-disabled={!file.download_url}
              key={file.file_id}
            >
              <FileText size={14} aria-hidden="true" />
              <span>{file.file_name}</span>
            </a>
          ))}
        </div>
      ) : null}
      {canBuild ? (
        <div className="hp-artifact-actions">
          <button type="button" disabled={loading} onClick={() => void primaryAction()}>
            {loading ? "加载中…" : artifacts?.length ? "打开 Artifact" : "生成 Artifact"}
          </button>
          {artifacts?.length ? (
            <button type="button" onClick={() => void createArtifact(message.id)}>
              再生成一个
            </button>
          ) : null}
        </div>
      ) : null}
    </MessagePrimitive.Root>
  );
}

export function HpThread({
  messages,
  activeRun,
  attachments,
  fileUploadEnabled,
  sendDisabled,
  onFilesSelected,
  onRemoveAttachment,
  onSend,
  onCancel,
}: HpThreadProps) {
  const runtime = useHpThreadRuntime({ messages, activeRun, sendDisabled, onSend, onCancel });
  const filesByMessageId = Object.fromEntries(
    messages.map((message) => [message.message_id, message.files ?? []]),
  );
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="hp-thread">
        <ThreadPrimitive.Viewport className="hp-thread__viewport" autoScroll>
          <ThreadPrimitive.Empty>
            <Flex align="center" justify="center" style={{ height: "100%", padding: 24 }}>
              <Text size="3" color="gray">
                开始新的对话吧
              </Text>
            </Flex>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages>
            {() => <HpMessageView filesByMessageId={filesByMessageId} />}
          </ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
        <ComposerPrimitive.Root className="hp-composer">
          {attachments.length ? (
            <div className="hp-composer__attachments" aria-label="待发送附件">
              {attachments.map((attachment) => (
                <div
                  className={`hp-attachment hp-attachment--${attachment.status}`}
                  key={attachment.localId}
                  title={attachment.error ?? attachment.name}
                >
                  <FileText size={14} aria-hidden="true" />
                  <span className="hp-attachment__name">{attachment.name}</span>
                  {attachment.status === "uploading" ? <Spinner size="1" /> : null}
                  {attachment.status === "ready" ? (
                    <span className="hp-attachment__status">已就绪</span>
                  ) : null}
                  {attachment.status === "failed" ? (
                    <span className="hp-attachment__status">上传失败</span>
                  ) : null}
                  <button
                    type="button"
                    className="hp-attachment__remove"
                    aria-label={`移除 ${attachment.name}`}
                    onClick={() => onRemoveAttachment(attachment.localId)}
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <ComposerPrimitive.Input
            className="hp-composer__input"
            placeholder="输入消息，Enter 发送"
            autoFocus
          />
          <Flex gap="2" align="center" className="hp-composer__actions">
            {fileUploadEnabled ? (
              <label className="hp-composer__attach" aria-label="添加附件">
                <Paperclip size={16} aria-hidden="true" />
                <span>附件</span>
                <input
                  type="file"
                  multiple
                  accept="text/*,.log,.md,.csv,.json,.yaml,.yml"
                  disabled={sendDisabled}
                  onChange={(event) => {
                    onFilesSelected(Array.from(event.currentTarget.files ?? []));
                    event.currentTarget.value = "";
                  }}
                />
              </label>
            ) : null}
            <ThreadPrimitive.If running={false}>
              <ComposerPrimitive.Send className="hp-composer__send">发送</ComposerPrimitive.Send>
            </ThreadPrimitive.If>
            <ThreadPrimitive.If running>
              <ComposerPrimitive.Cancel className="hp-composer__cancel">
                停止
              </ComposerPrimitive.Cancel>
            </ThreadPrimitive.If>
          </Flex>
        </ComposerPrimitive.Root>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
