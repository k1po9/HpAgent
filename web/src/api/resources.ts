/**
 * Typed endpoint wrappers (hpagent-web-api-contract.md §6–§8).
 *
 * Each intent-carrying mutation accepts an explicit `idempotencyKey` from the
 * caller; the client never generates or reuses one implicitly.
 */
import type { ApiClient } from "./client";
import type {
  HpConversation,
  HpConversationDetail,
  HpMessage,
  HpMessagePage,
  HpPage,
  HpRetryResult,
  HpRunSnapshot,
  HpSendResult,
} from "./types";

export interface SendMessageOptions {
  idempotencyKey: string;
  signal?: AbortSignal;
}

export class HpApi {
  constructor(private readonly client: ApiClient) {}

  async listConversations(cursor?: string | null): Promise<HpPage<HpConversation>> {
    const query = new URLSearchParams({ limit: "30" });
    if (cursor) {
      query.set("cursor", cursor);
    }
    return this.client.request<HpPage<HpConversation>>({
      method: "GET",
      path: `/api/v1/conversations?${query.toString()}`,
    });
  }

  async createConversation(idempotencyKey: string): Promise<{
    conversation: HpConversation;
    __idempotencyReplayed?: boolean;
  }> {
    return this.client.request<{ conversation: HpConversation }>({
      method: "POST",
      path: "/api/v1/conversations",
      body: { title: null },
      idempotencyKey,
    });
  }

  async getConversationDetail(id: string): Promise<HpConversationDetail> {
    return this.client.request<HpConversationDetail>({
      method: "GET",
      path: `/api/v1/conversations/${id}`,
    });
  }

  async renameConversation(
    id: string,
    title: string,
    etag: string,
    idempotencyKey: string,
  ): Promise<{ conversation: HpConversation }> {
    return this.client.request<{ conversation: HpConversation }>({
      method: "PATCH",
      path: `/api/v1/conversations/${id}`,
      body: { title },
      headers: { "If-Match": etag },
      idempotencyKey,
    });
  }

  async listMessages(conversationId: string, cursor?: string | null): Promise<HpMessagePage> {
    const query = new URLSearchParams({ limit: "50" });
    if (cursor) {
      query.set("cursor", cursor);
    }
    return this.client.request<HpMessagePage>({
      method: "GET",
      path: `/api/v1/conversations/${conversationId}/messages?${query.toString()}`,
    });
  }

  async sendMessage(
    conversationId: string,
    content: string,
    { idempotencyKey, signal }: SendMessageOptions,
  ): Promise<HpSendResult & { __idempotencyReplayed?: boolean }> {
    return this.client.request<HpSendResult>({
      method: "POST",
      path: `/api/v1/conversations/${conversationId}/messages`,
      body: { content },
      idempotencyKey,
      signal,
    });
  }

  async getRun(runId: string): Promise<HpRunSnapshot> {
    return this.client.request<HpRunSnapshot>({
      method: "GET",
      path: `/api/v1/runs/${runId}`,
    });
  }

  async cancelRun(runId: string, idempotencyKey: string): Promise<HpRunSnapshot> {
    return this.client.request<HpRunSnapshot>({
      method: "POST",
      path: `/api/v1/runs/${runId}/cancel`,
      body: {},
      idempotencyKey,
    });
  }

  async retryRun(
    runId: string,
    idempotencyKey: string,
  ): Promise<HpRetryResult & { __idempotencyReplayed?: boolean }> {
    return this.client.request<HpRetryResult>({
      method: "POST",
      path: `/api/v1/runs/${runId}/retry`,
      body: {},
      idempotencyKey,
    });
  }
}

/** Build a conversation/run resource path the SSE client can subscribe to. */
export function eventsUrl(runId: string): string {
  return `/api/v1/runs/${runId}/events`;
}

export function messageKey(message: HpMessage): string {
  return `${message.conversation_id}:${message.message_id}`;
}
