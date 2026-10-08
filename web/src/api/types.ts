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
  "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled";

export type AgentStrategy = "react" | "plan_and_execute";

export interface HpFileApproval {
  approval_id: string;
  run_id: string;
  operation_id: string;
  requested_at?: string;
  expires_at?: string;
  decided_at?: string | null;
  consumed_at?: string | null;
  action_summary: string;
  tool_name: string;
  status: "pending" | "approved" | "rejected" | "expired" | "cancelled" | "consumed";
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
  execution_id?: string;
  run_id: string;
  conversation_id: string;
  session_id: string | null;
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

export interface HpChatRunSnapshot {
  source_kind: "chat";
  run: HpRun;
  assistant_message: HpMessage;
}

export interface HpRunBranch {
  execution_id: string;
  parent_execution_id: string;
  branch_key: string;
  branch_attempt: number;
  status: HpRunStatus;
  result_ref: string | null;
  error_code: string | null;
  required: boolean;
  usage: Record<string, { used: number; reserved: number }>;
}

export interface HpWorkRunSnapshot {
  source_kind: "work";
  run: {
    source_kind: "work";
    run_id: string;
    execution_id: string;
    work_id: string;
    requirement_revision: number;
    work_control_epoch: number;
    version: number;
    created_at: string;
    updated_at: string;
    started_at: string | null;
    finished_at: string | null;
    failure_code: string | null;
    failure_message: string | null;
    conversation_id: null;
    session_id: null;
    status: HpRunStatus;
    strategy_kind: "deterministic" | "fixed_workflow" | "generic_agent";
    executor_key: string;
    result_json: Record<string, unknown> | null;
    budget: HpRunBudget | null;
    published_file?: HpFile | null;
    branches?: HpRunBranch[];
  };
}

export type HpRunSnapshot = HpChatRunSnapshot | HpWorkRunSnapshot;

export type HpTraceStatus = "running" | "completed" | "failed" | "cancelled" | "unknown";

export interface HpTraceRun {
  trace_run_id: string;
  run_id: string;
  conversation_id: string | null;
  work_id?: string | null;
  requirement_revision?: number | null;
  execution_id?: string | null;
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

export type HpPromptVisibility = "none" | "summary" | "full_safe";

export interface HpModelInputSummary {
  snapshot_id: string;
  content_hash: string;
  model_call_id: string;
  phase: string;
  fallback_attempt: number;
  endpoint_id: string;
  provider: string;
  model: string;
  api_format: string;
  created_at: string;
  message_count: number;
  tool_count: number;
}

export type HpModelInputList =
  | {
      visibility: "none";
      items: Array<Pick<HpModelInputSummary, "snapshot_id" | "content_hash" | "model_call_id">>;
    }
  | { visibility: "summary" | "full_safe"; items: HpModelInputSummary[] };

export interface HpModelInputDetail {
  visibility: Exclude<HpPromptVisibility, "none">;
  model_input: HpModelInputSummary & { provider_request_body?: Record<string, unknown> };
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
  conversation_id: string | null;
  source_message_id: string | null;
  kind: HpArtifactKind;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface HpArtifactVersion {
  producing_run_id?: string | null;
  producing_execution_id?: string | null;
  producing_operation_id?: string | null;
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

export interface HpWorkEvent {
  event_id: string;
  work_id: string;
  event_seq: number;
  event_type: string;
  requirement_revision: number;
  bounded_payload: Record<string, unknown>;
}

export interface HpRequirement {
  // GET includes persisted row metadata; mutation builders submit only domain fields.
  revision?: number;
  work_id?: string;
  account_id?: string;
  change_reason?: string;
  created_at?: string;
  source_message_id?: string | null;
  created_by?: string;
  command_id?: string;
  content_hash?: string;
  objective: string;
  capability_key: string;
  spec: Record<string, unknown>;
  constraints: string[];
  acceptance_criteria: Array<{ id: string; required: boolean; evidence_types: string[] }>;
  completion_mode: "deliverable" | "ongoing";
  timing: {
    schema_version: number;
    kind: string;
    timezone: string;
    due_at?: string;
    local_time?: string;
  };
  resource_requests: Array<Record<string, unknown>>;
  deliverable_policy: Record<string, unknown>;
}
export interface HpTaskRun {
  run_id: string;
  work_id: string;
  status: string;
  requirement_revision: number;
  work_control_epoch: number;
  result_json: { evidence?: Array<{ type: string; ref: string }> } | null;
  failure_code: string | null;
  created_at?: string;
}
export interface HpNotification {
  notification_id: string;
  work_id: string | null;
  run_id: string | null;
  created_at: string;
  payload: unknown;
  provider_receipt: { level: string } | null;
}
export interface HpWork {
  work_id: string;
  title: string;
  status: "active" | "pausing" | "paused" | "stopping" | "stopped" | "completed";
  row_version: number;
  current_requirement_revision: number;
  active_coordinator_run_id: string | null;
  conversation_ids: string[];
  control_epoch: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  stopped_at: string | null;
  schedule: { desired_enabled: boolean; next_due_at: string | null } | null;
  continuation: {
    kind: string;
    reason: string;
    due_at?: string;
    receipt_ref?: string;
    operation_ref?: string;
  };
  requirement: HpRequirement;
  budget: {
    limits: Record<string, number>;
    used: Record<string, number>;
    reserved: Record<string, number>;
    version: number;
  } | null;
  workspace_saves: Array<{
    run_id: string;
    state: string;
    requirement_revision: number;
    failure_code: string | null;
    entry_id?: string | null;
    operation_id?: string | null;
  }>;
  artifacts: Array<{
    artifact_id: string;
    artifact_version_id: string;
    role: "input" | "evidence" | "deliverable";
    status: string;
    accepted_for_revision: number | null;
    source_requirement_revision: number;
    producing_run_id?: string | null;
    file_id?: string | null;
    reference_id?: string;
  }>;
  deliveries: Array<{
    delivery_id: string;
    notification_id?: string;
    attempts?: number;
    state: string;
    channel: string;
    purpose: string;
    requirement_revision: number;
    last_error: string | null;
    provider_receipt: { level: string } | null;
  }>;
}

export interface HpWorkspaceNode {
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
    size_bytes: number | null;
  } | null;
}
export interface HpWorkspace {
  workspace_id: string;
  root_id: string;
  nodes: HpWorkspaceNode[];
}
export interface HpWorkspaceFilters {
  name?: string;
  summary?: string;
  content_type?: string;
  purpose?: string;
  work_id?: string;
  source_run_id?: string;
  from_date?: string;
  to_date?: string;
}
export interface HpWorkspaceSearchItem {
  node_id: string;
  name: string;
  file_id: string;
  destination_id?: string | null;
  content_type: string | null;
  size_bytes: number | null;
  purpose: string;
  source_run_id: string | null;
  source_work_id: string | null;
  revision: number | null;
  created_at?: string;
  summary?: string | null;
  summary_sha256?: string | null;
}
export interface HpWorkspaceSearchPage {
  items: HpWorkspaceSearchItem[];
  next_after: string | null;
}
export interface HpWorkspaceTrace {
  node_id: string;
  file_id: string;
  destination_id: string | null;
  current_revision: number | null;
  source_run_id: string | null;
  source_work_id: string | null;
  publication: { operation_id: string; status: string; run_id: string } | null;
  versions: Array<{ revision: number; file_id: string; operation_id: string }>;
  saves: Array<{ operation_id: string; source_kind: string; source_run_id: string | null }>;
  denials: Array<{ run_id: string; operation: string; reason: string; created_at: string }>;
  run_usage_truncated: boolean;
  run_usage: Array<{
    run_id: string;
    fixed_file_id: string | null;
    fixed_revision: number | null;
    fixed_at: string | null;
    materialized_at: string | null;
    first_read_at: string | null;
  }>;
}
