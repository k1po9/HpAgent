import { useEffect, useState } from "react";
import { Box, Button, Flex, Heading, Spinner, Text } from "@radix-ui/themes";
import { conversationDateGroup } from "./conversation/dateGroup";
import { Plus } from "lucide-react";
import type { HpConversation } from "../api/types";

interface ConversationSidebarProps {
  conversations: HpConversation[];
  activeConversationId: string | null;
  loading: boolean;
  creating: boolean;
  hasMore?: boolean;
  loadingMore?: boolean;
  error?: string | null;
  onLoadMore?: () => void;
  onRefresh?: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
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
  hasMore,
  loadingMore,
  error,
  onLoadMore,
  onRefresh,
  onSelect,
  onCreate,
}: ConversationSidebarProps) {
  const [filter, setFilter] = useState("");
  const [today, setToday] = useState(() => new Date());
  useEffect(() => {
    const refresh = () => setToday(new Date());
    const timer = setInterval(refresh, 60_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  const filtered = conversations.filter((c) =>
    c.title.toLocaleLowerCase().includes(filter.toLocaleLowerCase()),
  );
  const groups = ["今天", "昨天", "近 7 天", "更早"];
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

        <input
          className="hp-conversation-filter"
          aria-label="筛选已加载对话"
          placeholder="筛选已加载对话"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <Flex direction="column" gap="1" className="hp-sidebar__list">
          {error && (
            <div role="alert">
              <p>{error}</p>
              <button onClick={onRefresh}>从首批刷新</button>
            </div>
          )}
          {filter && !filtered.length && <p>已加载对话中没有匹配标题，仍可加载更多。</p>}
          {loading ? (
            <Flex align="center" justify="center" gap="2" className="hp-sidebar__hint">
              <Spinner size="1" />
              <Text size="2" color="gray">
                加载中…
              </Text>
            </Flex>
          ) : null}
          {!loading && !error && conversations.length === 0 ? (
            <Flex align="center" justify="center" className="hp-sidebar__hint">
              <Text size="2" color="gray">
                还没有对话，点击「新建」开始。
              </Text>
            </Flex>
          ) : null}
          {groups.map((group) => (
            <div key={group}>
              {filtered.some((c) => conversationDateGroup(c.updated_at, today) === group) && (
                <h3 className="hp-conversation-group">{group}</h3>
              )}
              {filtered
                .filter((c) => conversationDateGroup(c.updated_at, today) === group)
                .map((conversation) => (
                  <button
                    key={conversation.conversation_id}
                    title={conversation.title}
                    data-conversation-id={conversation.conversation_id}
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
            </div>
          ))}
          {hasMore && (
            <button disabled={loadingMore || loading} onClick={onLoadMore}>
              {loadingMore ? "加载中…" : "加载更多对话"}
            </button>
          )}
        </Flex>
      </Flex>
    </Box>
  );
}
