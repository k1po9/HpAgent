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
  HpModelInputDetail,
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

  async listFileCandidates(
    conversationId: string,
    before?: string | null,
  ): Promise<{
    items: HpFile[];
    next_before: string | null;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/conversations/${conversationId}/file-candidates${before ? `?before=${before}` : ""}`,
    });
  }

  async listResearchTasks(before?: string | null): Promise<{
    items: Array<{
      task_id: string;
      title: string;
      status: string;
      output_directory_id: string | null;
      output_directory_name: string | null;
      output_required: boolean;
      output_operation: string;
      output_entry_id: string | null;
      schedule_type: string;
      schedule_timezone: string;
      schedule_expression: string | null;
    }>;
    next_before: string | null;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/tasks${before ? `?before=${before}` : ""}`,
    });
  }

  async createResearchTask(
    title: string,
    objective: string,
    directoryId: string,
    idempotencyKey: string,
  ): Promise<{ task: { task_id: string } }> {
    return this.client.request({
      method: "POST",
      path: "/api/v1/tasks",
      idempotencyKey,
      body: { title, objective, output_directory_id: directoryId, output_required: true },
    });
  }

  async setResearchOutput(
    taskId: string,
    directoryId: string,
    operation: "create_child" | "update_content" = "create_child",
    entryId: string | null = null,
  ): Promise<void> {
    await this.client.request({
      method: "PUT",
      path: `/api/v1/tasks/${taskId}/output`,
      body: {
        output_directory_id: directoryId,
        required: true,
        operation,
        output_entry_id: entryId,
      },
    });
  }

  async triggerResearchTask(taskId: string, idempotencyKey: string): Promise<void> {
    await this.client.request({
      method: "POST",
      path: `/api/v1/tasks/${taskId}/runs`,
      body: {},
      idempotencyKey,
    });
  }

  async grantResearchInput(taskId: string, nodeId: string, recursive: boolean): Promise<void> {
    await this.client.request({
      method: "POST",
      path: `/api/v1/tasks/${taskId}/resources`,
      body: { node_id: nodeId, operations: ["list_metadata", "read_content"], recursive },
    });
  }

  async setResearchSchedule(
    taskId: string,
    timezone: string,
    expression: string,
    idempotencyKey: string,
  ): Promise<void> {
    await this.client.request({
      method: "PUT",
      path: `/api/v1/tasks/${taskId}/schedule`,
      idempotencyKey,
      body: { schedule_type: "daily", timezone, expression, enabled: true },
    });
  }

  async listResearchRuns(
    taskId: string,
    before?: string | null,
  ): Promise<{
    items: Array<{
      run_id: string;
      status: string;
      failure_code: string | null;
      save_status: string | null;
      save_entry_id: string | null;
      save_failure_code: string | null;
      published_file: {
        file_id: string;
        file_name: string;
        download_url: string;
      } | null;
    }>;
    next_before: string | null;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/tasks/${taskId}/runs${before ? `?before=${before}` : ""}`,
    });
  }

  async getResearchReport(
    taskId: string,
    runId: string,
  ): Promise<{
    report: { report_markdown: string };
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/tasks/${taskId}/runs/${runId}/report`,
    });
  }

  async getWorkspace(): Promise<{
    workspace_id: string;
    root_id: string;
    nodes: Array<{
      node_id: string;
      parent_id: string | null;
      kind: "directory" | "file";
      name: string;
      file_id: string | null;
      destination_id: string | null;
      revision: number | null;
      source: {
        purpose: "input" | "output";
        conversation_id: string | null;
        run_id: string | null;
        sha256: string;
        size_bytes: number;
      } | null;
    }>;
  }> {
    return this.client.request({ method: "GET", path: "/api/v1/workspace" });
  }

  async searchWorkspace(
    filters: {
      name?: string;
      content_type?: string;
      purpose?: string;
      task_id?: string;
      source_run_id?: string;
      from_date?: string;
      to_date?: string;
      summary?: string;
    },
    after?: string | null,
  ): Promise<{
    items: Array<{
      node_id: string;
      name: string;
      file_id: string;
      content_type: string | null;
      size_bytes: number | null;
      purpose: string;
      source_run_id: string | null;
      source_task_id: string | null;
      revision: number | null;
    }>;
    next_after: string | null;
  }> {
    const query = new URLSearchParams({ limit: "50" });
    for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
    if (after) query.set("after", after);
    return this.client.request({ method: "GET", path: `/api/v1/workspace/search?${query}` });
  }

  async getWorkspaceSpace(): Promise<{
    physical_files: number;
    physical_bytes: number;
    files_with_active_entry: number;
    bytes_with_active_entry: number;
  }> {
    return this.client.request({ method: "GET", path: "/api/v1/workspace/space" });
  }

  async getFileRetention(fileId: string): Promise<{
    physical_bytes: number;
    references: Record<string, number>;
    explanation: string;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/workspace/files/${fileId}/retention`,
    });
  }

  async listConversationResources(conversationId: string): Promise<{
    grants: Array<{
      grant_id: string;
      node_id: string;
      name: string;
      kind: "file" | "directory";
      operation:
        "list_metadata" | "read_content" | "create_child" | "update_content" | "delete_entry";
      recursive: boolean;
    }>;
    attachments: Array<{ file_id: string; name: string; available: boolean }>;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/conversations/${conversationId}/resources`,
    });
  }

  async grantConversationResource(
    conversationId: string,
    nodeId: string,
    operations: Array<
      "list_metadata" | "read_content" | "create_child" | "update_content" | "delete_entry"
    >,
    recursive: boolean,
  ): Promise<{ grant_ids: string[] }> {
    return this.client.request({
      method: "POST",
      path: `/api/v1/conversations/${conversationId}/resources`,
      body: { node_id: nodeId, operations, recursive },
    });
  }

  async revokeConversationResource(
    conversationId: string,
    grantId: string,
  ): Promise<{
    affected_runs: Array<{ run_id: string; stop_state: "stopping" | "stopped" }>;
  }> {
    return this.client.request({
      method: "DELETE",
      path: `/api/v1/conversations/${conversationId}/resources/${grantId}`,
    });
  }

  async revokeConversationAttachment(
    conversationId: string,
    fileId: string,
  ): Promise<{
    affected_runs: Array<{ run_id: string; stop_state: "stopping" | "stopped" }>;
  }> {
    return this.client.request({
      method: "DELETE",
      path: `/api/v1/conversations/${conversationId}/attachments/${fileId}`,
    });
  }

  async listRunResources(
    runId: string,
    after?: string | null,
  ): Promise<{
    count: number;
    next: string | null;
    candidates: Array<{
      node_id: string;
      logical_name: string;
      name: string;
      content_type: string | null;
      size_bytes: number | null;
      fixed: boolean;
      read: boolean;
    }>;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/runs/${runId}/resources${after ? `?after=${encodeURIComponent(after)}` : ""}`,
    });
  }

  async createWorkspaceDirectory(parentId: string, name: string): Promise<{ node_id: string }> {
    return this.client.request({
      method: "POST",
      path: "/api/v1/workspace/directories",
      body: { parent_id: parentId, name },
    });
  }

  async saveWorkspaceFile(
    parentId: string,
    fileId: string,
    name: string,
    idempotencyKey: string,
  ): Promise<{ node_id: string }> {
    return this.client.request({
      method: "POST",
      path: "/api/v1/workspace/files",
      body: { parent_id: parentId, file_id: fileId, name },
      idempotencyKey,
    });
  }

  async getWorkspaceVersions(nodeId: string): Promise<{
    current: {
      node_id: string;
      destination_id: string | null;
      revision: number | null;
      file_id: string;
      sha256: string;
    };
    revisions: Array<{
      revision: number;
      file_id: string;
      sha256: string;
      operation_id: string;
      created_at: string;
      source: {
        purpose: "input" | "output";
        conversation_id: string | null;
        run_id: string | null;
      };
    }>;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/workspace/nodes/${nodeId}/versions`,
    });
  }

  async listRunPublishedFiles(runId: string): Promise<{
    files: Array<{ file_id: string; name: string; sha256: string }>;
  }> {
    return this.client.request({
      method: "GET",
      path: `/api/v1/runs/${runId}/published-files`,
    });
  }

  async upgradeWorkspaceFile(nodeId: string): Promise<{
    node_id: string;
    destination_id: string;
    revision: number;
    file_id: string;
    sha256: string;
  }> {
    return this.client.request({
      method: "POST",
      path: `/api/v1/workspace/nodes/${nodeId}/upgrade`,
      body: {},
    });
  }

  async updateWorkspaceFile(
    nodeId: string,
    runId: string,
    fileId: string,
    expectedRevision: number,
    expectedSha256: string,
    operationId: string,
  ): Promise<{ node_id: string; destination_id: string; revision: number; file_id: string }> {
    return this.client.request({
      method: "POST",
      path: `/api/v1/workspace/nodes/${nodeId}/versions`,
      body: {
        run_id: runId,
        file_id: fileId,
        expected_revision: expectedRevision,
        expected_sha256: expectedSha256,
      },
      idempotencyKey: operationId,
    });
  }

  async previewWorkspaceNode(nodeId: string): Promise<{
    preview_token: string;
    potentially_affected_runs: string[];
  }> {
    return this.client.request({ method: "GET", path: `/api/v1/workspace/nodes/${nodeId}/impact` });
  }

  async moveWorkspaceNode(
    nodeId: string,
    parentId: string,
    name: string,
    previewToken: string,
  ): Promise<{ node_id: string }> {
    return this.client.request({
      method: "PATCH",
      path: `/api/v1/workspace/nodes/${nodeId}`,
      body: { parent_id: parentId, name, preview_token: previewToken },
    });
  }

  async removeWorkspaceNode(nodeId: string, previewToken: string): Promise<void> {
    await this.client.request({
      method: "DELETE",
      path: `/api/v1/workspace/nodes/${nodeId}`,
      headers: { "X-Workspace-Preview": previewToken },
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
        content_type: file.name.toLowerCase().endsWith(".md")
          ? file.type === "text/x-markdown" || !file.type
            ? "text/markdown"
            : file.type
          : file.type || "text/plain",
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

  async getModelInput(snapshotId: string): Promise<HpModelInputDetail> {
    return this.client.request<HpModelInputDetail>({
      method: "GET",
      path: `/api/v1/model-inputs/${snapshotId}`,
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
