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
}

export type HpRunStatus =
  "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";

export type AgentStrategy = "react" | "plan_and_execute";

export interface HpFailure {
  code: string;
  message: string;
  retryable: boolean;
}

export interface HpRun {
  run_id: string;
  conversation_id: string;
  session_id: string;
  trigger_message_id: string;
  retry_of_run_id: string | null;
  status: HpRunStatus;
  failure: HpFailure | null;
  version: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface HpRunSnapshot {
  run: HpRun;
  assistant_message: HpMessage;
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
