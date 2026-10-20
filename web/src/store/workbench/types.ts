import { HpApi } from "../../api/resources";
import { type RunProgress } from "../../sse/runFeed";
import {
  type AgentStrategy,
  type HpConversation,
  type HpMessage,
  type HpFile,
  type HpRun,
} from "../../api/types";

export interface WorkbenchDeps {
  api: HpApi;
  /** fetch override for the SSE subscription (tests swap this for a mock). */
  fetchImpl?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  /** Session invalidated mid-stream: defaults to flipping the app to login. */
  onAuthExpired?: () => void;
}

export interface UploadAttachment {
  localId: string;
  name: string;
  size: number;
  status: UploadAttachmentStatus;
  fileId: string | null;
  file: HpFile | null;
  error: string | null;
  existing?: boolean;
}

export interface WorkbenchState {
  // Conversation list
  conversations: HpConversation[];
  conversationsLoaded: boolean;
  loadingConversations: boolean;
  conversationCursor: string | null;
  hasMoreConversations: boolean;
  loadingMoreConversations: boolean;
  activeConversation: HpConversation | null;
  loadingFileCandidates: boolean;
  fileCandidatesError: string | null;
  creatingConversation: boolean;
  conversationsError: string | null;
  // Active conversation
  activeConversationId: string | null;
  messages: HpMessage[];
  messageCursor: string | null;
  hasMoreMessages: boolean;
  loadingMessages: boolean;
  loadingMoreMessages: boolean;
  attachments: UploadAttachment[];
  fileCandidates: HpFile[];
  fileCandidatesNext: string | null;
  // Active Run
  activeRun: HpRun | null;
  activeRunError: string | null;
  /** Volatile `run.progress` hint; shown only in the run-status area (E-06). */
  activeRunProgress: RunProgress | null;
  /** True when the SSE stream degraded/connection was lost (recovering by poll). */
  degraded: boolean;
  polling: boolean;
  sending: boolean;
  stopping: boolean;
  agentStrategy: AgentStrategy;
  // Transient UI error (dismissible)
  error: string | null;
  // Bumped whenever a new Run becomes authoritative so stale pollers stop.
  pollGeneration: number;
  loadConversations: () => Promise<void>;
  loadMoreConversations: () => Promise<void>;
  ensureConversation: () => Promise<string | null>;
  leaveConversation: () => void;
  renameActiveConversation: (title: string) => Promise<boolean>;
  createConversation: () => Promise<void>;
  selectConversation: (id: string, refresh?: boolean) => Promise<void>;
  loadMoreMessages: () => Promise<void>;
  addAttachments: (files: File[]) => Promise<void>;
  selectExistingFile: (file: HpFile) => void;
  loadFileCandidates: (more?: boolean) => Promise<void>;
  removeAttachment: (localId: string) => Promise<void>;
  pendingSendIds: string[];
  pendingSendContent: Record<string, string>;
  confirmPendingSend: () => Promise<boolean>;
  sendMessage: (content: string, confirm?: boolean) => Promise<boolean>;
  setAgentStrategy: (strategy: AgentStrategy) => void;
  stopRun: () => Promise<void>;
  retryRun: () => Promise<void>;
  refreshActiveRun: () => Promise<void>;
  clearError: () => void;
  reset: () => void;
}

export type UploadAttachmentStatus = "uploading" | "ready" | "failed";
