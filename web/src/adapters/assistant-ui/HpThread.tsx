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
} from "@assistant-ui/react";
import { Flex, Text } from "@radix-ui/themes";
import ReactMarkdown from "react-markdown";
import type { HpMessage, HpRun } from "../../api/types";
import { useHpThreadRuntime } from "./runtime";

export interface HpThreadProps {
  messages: HpMessage[];
  activeRun: HpRun | null;
  onSend: (content: string) => void;
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
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

function HpMessageView() {
  return (
    <MessagePrimitive.Root className="hp-msg">
      <MessagePrimitive.If user>
        <span className="hp-msg__marker hp-msg__marker--user" aria-hidden="true" />
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <span className="hp-msg__marker hp-msg__marker--assistant" aria-hidden="true" />
      </MessagePrimitive.If>
      <MessagePrimitive.Parts components={{ Text: HpTextPart }} />
    </MessagePrimitive.Root>
  );
}

export function HpThread({ messages, activeRun, onSend, onCancel }: HpThreadProps) {
  const runtime = useHpThreadRuntime({ messages, activeRun, onSend, onCancel });
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
          <ThreadPrimitive.Messages>{() => <HpMessageView />}</ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
        <ComposerPrimitive.Root className="hp-composer">
          <ComposerPrimitive.Input
            className="hp-composer__input"
            placeholder="输入消息，Enter 发送"
            autoFocus
          />
          <Flex gap="2" align="center" className="hp-composer__actions">
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
