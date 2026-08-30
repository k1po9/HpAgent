/**
 * Typed endpoint wrappers (hpagent-web-api-contract.md §6–§8).
 *
 * Each intent-carrying mutation accepts an explicit `idempotencyKey` from the
 * caller; the client never generates or reuses one implicitly.
 */
import type { ApiClient } from "./client";
import type {
  AgentStrategy,
  HpConversation,
  HpConversationDetail,
  HpMessage,
  HpMessagePage,
  HpPage,
  HpRetryResult,
  HpRunSnapshot,
  HpTraceTree,
  HpSendResult,
  HpArtifact,
  HpArtifactSummary,
  HpArtifactVersion,
  HpFile,
} from "./types";

export interface SendMessageOptions {
  idempotencyKey: string;
  signal?: AbortSignal;
  agentStrategy?: AgentStrategy;
  fileIds?: string[];
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
    { idempotencyKey, signal, agentStrategy, fileIds }: SendMessageOptions,
  ): Promise<HpSendResult & { __idempotencyReplayed?: boolean }> {
    return this.client.request<HpSendResult>({
      method: "POST",
      path: `/api/v1/conversations/${conversationId}/messages`,
      body: {
        content,
        ...(agentStrategy ? { agent_strategy: agentStrategy } : {}),
        ...(fileIds?.length ? { file_ids: fileIds } : {}),
      },
      idempotencyKey,
      signal,
    });
  }

  async createUpload(
    conversationId: string,
    file: File,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<{ file: HpFile; content_url: string }> {
    return this.client.request({
      method: "POST",
      path: `/api/v1/conversations/${conversationId}/uploads`,
      body: {
        file_name: file.name,
        size_bytes: file.size,
        content_type: file.type || "text/plain",
      },
      idempotencyKey,
      signal,
    });
  }

  async uploadContent(contentUrl: string, file: File, signal?: AbortSignal): Promise<HpFile> {
    const result = await this.client.request<{ file: HpFile }>({
      method: "PUT",
      path: contentUrl,
      rawBody: file,
      signal,
    });
    return result.file;
  }

  async deleteFile(fileId: string): Promise<void> {
    await this.client.request<void>({
      method: "DELETE",
      path: `/api/v1/files/${fileId}`,
    });
  }

  async getRun(runId: string): Promise<HpRunSnapshot> {
    return this.client.request<HpRunSnapshot>({
      method: "GET",
      path: `/api/v1/runs/${runId}`,
    });
  }

  async getRunTrace(runId: string): Promise<HpTraceTree> {
    return this.client.request<HpTraceTree>({
      method: "GET",
      path: `/api/v1/runs/${runId}/trace`,
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

  async listMessageArtifacts(messageId: string): Promise<{ items: HpArtifactSummary[] }> {
    return this.client.request({ method: "GET", path: `/api/v1/messages/${messageId}/artifacts` });
  }

  async createArtifact(
    messageId: string,
    instruction: string | null,
    idempotencyKey: string,
  ): Promise<{ artifact: HpArtifact; version: HpArtifactVersion }> {
    return this.client.request({
      method: "POST",
      path: `/api/v1/messages/${messageId}/artifacts`,
      body: { instruction },
      idempotencyKey,
    });
  }

  async getArtifact(
    artifactId: string,
  ): Promise<{ artifact: HpArtifact; latest_version: HpArtifactVersion | null }> {
    return this.client.request({ method: "GET", path: `/api/v1/artifacts/${artifactId}` });
  }

  async listArtifactVersions(
    artifactId: string,
  ): Promise<{ artifact: HpArtifact; items: HpArtifactVersion[] }> {
    return this.client.request({ method: "GET", path: `/api/v1/artifacts/${artifactId}/versions` });
  }

  async createArtifactVersion(
    artifactId: string,
    instruction: string,
    idempotencyKey: string,
  ): Promise<{ artifact: HpArtifact; version: HpArtifactVersion }> {
    return this.client.request({
      method: "POST",
      path: `/api/v1/artifacts/${artifactId}/versions`,
      body: { instruction },
      idempotencyKey,
    });
  }

  async getArtifactVersion(versionId: string): Promise<{ version: HpArtifactVersion }> {
    return this.client.request({ method: "GET", path: `/api/v1/artifact-versions/${versionId}` });
  }
}

/** Build a conversation/run resource path the SSE client can subscribe to. */
export function eventsUrl(runId: string): string {
  return `/api/v1/runs/${runId}/events`;
}

export function messageKey(message: HpMessage): string {
  return `${message.conversation_id}:${message.message_id}`;
}
