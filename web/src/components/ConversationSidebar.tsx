import { Box, Button, Flex, Heading, Spinner, Text } from "@radix-ui/themes";
import { Plus, LogOut } from "lucide-react";
import type { HpConversation } from "../api/types";

interface ConversationSidebarProps {
  conversations: HpConversation[];
  activeConversationId: string | null;
  loading: boolean;
  creating: boolean;
  accountName: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onSignOut: () => void;
}

/**
 * Conversation list / create / switch (phase-e E-03).
 *
 * The list is authoritative from the API; backend IDs are the stable keys and a
 * refresh rebuilds the view entirely.
 */
export function ConversationSidebar({
  conversations,
  activeConversationId,
  loading,
  creating,
  accountName,
  onSelect,
  onCreate,
  onSignOut,
}: ConversationSidebarProps) {
  return (
    <Box className="hp-sidebar">
      <Flex direction="column" style={{ height: "100%" }}>
        <Flex justify="between" align="center" className="hp-sidebar__header">
          <Heading as="h2" size="3" weight="bold">
            对话
          </Heading>
          <Button
            size="1"
            variant="soft"
            onClick={onCreate}
            disabled={creating}
            aria-label="新建对话"
          >
            <Plus size={14} />
            {creating ? "创建中…" : "新建"}
          </Button>
        </Flex>

        <Flex direction="column" gap="1" className="hp-sidebar__list">
          {loading ? (
            <Flex align="center" justify="center" gap="2" className="hp-sidebar__hint">
              <Spinner size="1" />
              <Text size="2" color="gray">
                加载中…
              </Text>
            </Flex>
          ) : null}
          {!loading && conversations.length === 0 ? (
            <Flex align="center" justify="center" className="hp-sidebar__hint">
              <Text size="2" color="gray">
                还没有对话，点击「新建」开始。
              </Text>
            </Flex>
          ) : null}
          {conversations.map((conversation) => (
            <button
              key={conversation.conversation_id}
              type="button"
              className={`hp-conv ${conversation.conversation_id === activeConversationId ? "hp-conv--active" : ""}`}
              onClick={() => onSelect(conversation.conversation_id)}
              aria-current={
                conversation.conversation_id === activeConversationId ? "true" : undefined
              }
            >
              <Text size="2" truncate>
                {conversation.title || "未命名对话"}
              </Text>
            </button>
          ))}
        </Flex>

        <Flex justify="between" align="center" className="hp-sidebar__footer">
          <Text size="1" color="gray" truncate title={accountName}>
            {accountName}
          </Text>
          <Button size="1" variant="ghost" color="gray" onClick={onSignOut} aria-label="退出登录">
            <LogOut size={14} />
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
}
