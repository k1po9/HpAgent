/**
 * Canonical business DTOs (hpagent-web-api-contract.md §5).
 *
 * These are the project's own types. assistant-ui types are NEVER imported
 * here; the adapter in `adapters/assistant-ui/` is the only place that maps
 * them (contract §14.1).
 */

export type ConversationStatus = "active";

export interface HpConversation {
  conversation_id: string;
  title: string;
  status: ConversationStatus;
  last_message_seq: number;
  metadata_version: number;
  created_at: string;
  updated_at: string;
}

export type HpMessageRole = "user" | "assistant";

export type HpMessageStatus =
  | "accepted" // user message confirmed by the server
  | "pending" // assistant: queued / running / cancelling
  | "completed"
  | "failed"
  | "aborted";

export type HpFileStatus = "uploading" | "ready" | "rejected" | "deleted";

export interface HpFile {
  file_id: string;
  file_name: string;
  purpose: "input" | "output";
  status: HpFileStatus;
  size_bytes: number | null;
  content_type: string | null;
  encoding: string | null;
  sha256: string | null;
  failure_code: string | null;
  download_url: string | null;
}

export interface HpMessage {
  message_id: string;
  conversation_id: string;
  role: HpMessageRole;
  status: HpMessageStatus;
  content: string | null;
  sequence: number;
  client_request_id: string | null;
  produced_by_run_id: string | null;
  created_at: string;
  completed_at: string | null;
  files?: HpFile[];
}

export type HpRunStatus =
  "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";

export type AgentStrategy = "react" | "plan_and_execute";

export interface HpFileApproval {
  approval_id: string;
  run_id: string;
  operation_id: string;
  action_summary: string;
  tool_name: string;
  status: "pending" | "approved" | "rejected" | "expired" | "cancelled" | "consumed";
  logical_path: string | null;
  expected_revision: number | null;
}

export interface HpPersistentFileDestination {
  logical_path: string;
  current_revision: number;
  current_file_id: string;
  current_sha256: string;
  last_operation_id: string;
}

export interface HpFailure {
  code: string;
  message: string;
  retryable: boolean;
}

export interface HpTokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface HpUsageCounter {
  used: number;
  reserved: number;
  limit: number;
}

export interface HpRunBudget {
  status: "ok" | "exhausted" | "closed";
  mode: "off" | "observe" | "enforce";
  policy_version: string;
  tokens: { input: HpUsageCounter; output: HpUsageCounter; total: HpUsageCounter };
  model_calls: {
    settled: number;
    in_flight: number;
    unmetered: number;
    total_attempts: number;
    limit: number;
  };
  by_source: Record<"provider" | "measured" | "estimated", HpTokenUsage>;
  usage_state: "none" | "in_flight" | "complete" | "partial";
  usage_quality: "none" | "provider" | "estimated" | "mixed";
  has_estimates: boolean;
  model_total_tokens_used: number;
  model_total_tokens_limit: number;
  tool_calls_used: number;
  tool_calls_limit: number;
  bytes_scanned_used: number;
  bytes_scanned_limit: number;
}

export interface HpRun {
  run_id: string;
  conversation_id: string;
  session_id: string;
  trigger_message_id: string;
  retry_of_run_id: string | null;
  agent_strategy: AgentStrategy;
  status: HpRunStatus;
  failure: HpFailure | null;
  version: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  budget: HpRunBudget | null;
}

export interface HpRunSnapshot {
  run: HpRun;
  assistant_message: HpMessage;
}

export type HpTraceStatus = "running" | "completed" | "failed" | "cancelled";

export interface HpTraceRun {
  trace_run_id: string;
  run_id: string;
  conversation_id: string;
  strategy: string;
  status: HpTraceStatus;
  started_at: string;
  ended_at: string | null;
  metadata: Record<string, unknown>;
}

export interface HpTraceEvent {
  trace_event_id: string;
  trace_run_id: string;
  parent_event_id: string | null;
  event_type: string;
  name: string;
  status: HpTraceStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown>;
}

export interface HpTraceEventNode {
  event: HpTraceEvent;
  children: HpTraceEventNode[];
}

export interface HpTraceTree {
  run: HpTraceRun;
  roots: HpTraceEventNode[];
}

export interface HpActiveRun {
  run: HpRun;
  assistant_message: HpMessage;
}

export interface HpConversationDetail {
  conversation: HpConversation;
  active_run: HpActiveRun | null;
}

export interface HpSendResult {
  user_message: HpMessage;
  assistant_message: HpMessage;
  run: HpRun;
  events_url: string;
}

export interface HpRetryResult {
  source_run_id: string;
  assistant_message: HpMessage;
  run: HpRun;
  events_url: string;
}

export interface HpPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface HpMessagePage extends HpPage<HpMessage> {
  conversation_last_message_seq: number;
}

export type HpArtifactKind = "html";
export type HpArtifactVersionStatus = "queued" | "running" | "completed" | "failed";

export interface HpArtifact {
  artifact_id: string;
  conversation_id: string;
  source_message_id: string;
  kind: HpArtifactKind;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface HpArtifactVersion {
  artifact_version_id: string;
  artifact_id: string;
  version: number;
  parent_version_id: string | null;
  status: HpArtifactVersionStatus;
  instruction: string | null;
  html: string | null;
  failure: { code: string; message: string } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface HpArtifactSummary {
  artifact: HpArtifact;
  latest_version: HpArtifactVersion | null;
}

/** Stable error codes (contract §10.2). */
export type HpErrorCode =
  | "unauthenticated"
  | "csrf_invalid"
  | "resource_not_found"
  | "conversation_busy"
  | "idempotency_conflict"
  | "version_conflict"
  | "precondition_required"
  | "empty_message"
  | "message_too_large"
  | "run_not_cancellable"
  | "run_not_retryable"
  | "run_retry_not_safe"
  | "invalid_cursor"
  | "cursor_expired"
  | "service_unavailable"
  | "validation_error"
  | "malformed_request"
  | string;

export interface HpErrorDetail {
  code: HpErrorCode;
  message: string;
  request_id: string | null;
  retryable: boolean;
  details: Record<string, unknown>;
}

/** Typed application error for the API client. */
export class HpCommandError extends Error {
  readonly status: number;
  readonly error: HpErrorDetail;
  readonly idempotencyReplayed: boolean;

  constructor(status: number, error: HpErrorDetail, replayed = false) {
    super(error.message);
    this.name = "HpCommandError";
    this.status = status;
    this.error = error;
    this.idempotencyReplayed = replayed;
  }

  get code(): HpErrorCode {
    return this.error.code;
  }
}
