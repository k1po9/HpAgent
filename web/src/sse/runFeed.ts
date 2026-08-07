/**
 * Run event feed (phase-e E-06).
 *
 * Subscribes one Run's SSE stream and turns raw frames into typed callbacks,
 * enforcing the contract §12.2 invariants:
 *
 * - Dedup by event_id (terminal publisher retries reuse the same ID).
 * - Online events (run.started / message.delta / run.progress) share one
 *   stream_id/event_seq sequence. The first non-null event_seq establishes the
 *   baseline; later events must be seq+1. Reordered events within a small window
 *   are buffered until the gap fills; a jump beyond the window or a stream_id
 *   change degrades the stream permanently — delta assembly stops.
 * - `run.progress` is a separate volatile callback; it never touches Message
 *   content.
 * - Terminal events carry the committed database RunSnapshot; the snapshot is
 *   the authoritative replacement for any locally assembled delta buffer.
 * - `stream.degraded` / `auth.expired` close the stream with a stable reason.
 *
 * Recovery (contract §13.2) is the caller's job: on degraded the feed stops and
 * the store queries `GET /api/v1/runs/{run_id}`, polling while the Run is still
 * active.
 */
import { eventsUrl } from "../api/resources";
import type { HpRunSnapshot, HpRunStatus } from "../api/types";
import { openSseStream, SseConnectError, type SseFrame, type SseStreamHandle } from "./sseClient";

export const SSE_DEGRADE_REASONS = [
  "handshake_buffer_overflow",
  "sequence_gap",
  "redis_unavailable",
  "upstream_disconnected",
] as const;
export type SseDegradeReason = (typeof SSE_DEGRADE_REASONS)[number] | "connect_failed";

export interface RunProgress {
  phase: string;
  summary: string;
}

/** The contract domain event envelope (§12.2). */
export interface SseEnvelope {
  schema_version: number;
  event_id: string;
  event_type: string;
  conversation_id: string | null;
  run_id: string;
  message_id: string | null;
  stream_id: string | null;
  event_seq: number | null;
  occurred_at: string | null;
  payload: Record<string, unknown>;
}

export interface RunFeedHandlers {
  /** Initial committed snapshot (first frame of a healthy connection). */
  onSnapshot?: (snapshot: HpRunSnapshot) => void;
  /** Volatile delta text for one pending assistant Message. */
  onDelta?: (messageId: string, delta: string) => void;
  /** Volatile progress hint; display in the run-status area only. */
  onProgress?: (progress: RunProgress) => void;
  /** Non-terminal Run status change (queued/running/cancelling). */
  onStatus?: (status: HpRunStatus) => void;
  /** Terminal RunSnapshot (run.completed/failed/cancelled); replace local state. */
  onTerminal?: (snapshot: HpRunSnapshot) => void;
  /** Stream degraded (stable reason) or connect failure — stop delta assembly. */
  onDegraded?: (reason: string) => void;
  /** Session invalidated mid-stream (auth.expired / 401 connect). */
  onAuthExpired?: () => void;
}

export interface OpenRunFeedOptions {
  signal?: AbortSignal;
  fetchImpl?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}

export interface RunFeed {
  readonly runId: string;
  /** True once the stream degraded: delta assembly must stay stopped. */
  readonly degraded: boolean;
  /** Close the stream cleanly (conversation switch, stop, unmount). */
  close: () => void;
  /** Resolves when the feed ends (terminal/degraded/auth.expired/close/error). */
  readonly done: Promise<void>;
}

class DegradeError extends Error {
  readonly reason: string;
  constructor(reason: string) {
    super(`SSE stream degraded: ${reason}`);
    this.name = "DegradeError";
    this.reason = reason;
  }
}

/**
 * Orders online events within one Sink lifetime (contract §12.2).
 *
 * null-seq database projections (snapshot, run.status, terminal) pass through
 * untouched; only non-null stream_id/event_seq events are checked.
 */
class StreamSequencer {
  private streamId: string | null = null;
  private nextSeq: number | null = null;
  private readonly buffered = new Map<number, SseEnvelope>();
  /** Reordering window: beyond this a gap is declared unfillable. */
  private static readonly MAX_GAP = 16;

  accept(event: SseEnvelope): { deliver: SseEnvelope[]; duplicate: boolean } {
    if (event.stream_id === null || event.event_seq === null) {
      return { deliver: [event], duplicate: false };
    }
    if (this.streamId === null) {
      this.streamId = event.stream_id;
      this.nextSeq = event.event_seq + 1;
      return { deliver: [event], duplicate: false };
    }
    if (event.stream_id !== this.streamId) {
      throw new DegradeError("sequence_gap");
    }
    // `nextSeq` is non-null once streamId is set; a local avoids re-narrowing
    // `this.nextSeq` across property mutations (TS strict null checks).
    const expected = this.nextSeq ?? 0;
    const seq = event.event_seq;
    if (seq < expected) {
      return { deliver: [], duplicate: true };
    }
    if (seq === expected) {
      let next = seq + 1;
      const deliver = [event];
      while (this.buffered.has(next)) {
        const buffered = this.buffered.get(next)!;
        this.buffered.delete(next);
        deliver.push(buffered);
        next += 1;
      }
      this.nextSeq = next;
      return { deliver, duplicate: false };
    }
    if (seq - expected > StreamSequencer.MAX_GAP || this.buffered.has(seq)) {
      throw new DegradeError("sequence_gap");
    }
    this.buffered.set(seq, event);
    if (this.buffered.size > StreamSequencer.MAX_GAP) {
      throw new DegradeError("sequence_gap");
    }
    return { deliver: [], duplicate: false };
  }
}

function isRunSnapshot(value: unknown): value is HpRunSnapshot {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as HpRunSnapshot).run !== undefined &&
    (value as HpRunSnapshot).assistant_message !== undefined
  );
}

function isRunStatus(value: unknown): value is HpRunStatus {
  return (
    value === "queued" ||
    value === "running" ||
    value === "cancelling" ||
    value === "completed" ||
    value === "failed" ||
    value === "cancelled"
  );
}

export function openRunFeed(
  runId: string,
  handlers: RunFeedHandlers,
  options: OpenRunFeedOptions = {},
): RunFeed {
  let degraded = false;
  let ended = false; // a terminal/degraded/auth decision has been made
  let stream: SseStreamHandle | null = null;
  const seenEventIds = new Set<string>();
  const sequencer = new StreamSequencer();
  const controller = new AbortController();
  const signal = controller.signal;
  const externalSignal = options.signal;
  const onExternalAbort = (): void => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onExternalAbort, { once: true });
  }

  let doneResolve!: () => void;
  let doneReject!: (err: unknown) => void;
  const done = new Promise<void>((resolve, reject) => {
    doneResolve = resolve;
    doneReject = reject;
  });

  const close = (): void => {
    ended = true;
    controller.abort();
    if (externalSignal) externalSignal.removeEventListener("abort", onExternalAbort);
  };

  const degrade = (reason: string): void => {
    if (degraded || ended) return;
    degraded = true;
    ended = true;
    handlers.onDegraded?.(reason);
    controller.abort();
  };

  const handleFrame = (frame: SseFrame): void => {
    if (degraded || ended) return;
    if (!frame.data) return; // keepalive comment / empty frame
    let envelope: SseEnvelope;
    try {
      envelope = JSON.parse(frame.data) as SseEnvelope;
    } catch {
      return; // malformed frame: record and ignore, never treat as success
    }
    if (typeof envelope.event_type !== "string" || !envelope.event_type) return;
    if (seenEventIds.has(envelope.event_id)) return; // dedup
    seenEventIds.add(envelope.event_id);
    route(envelope);
  };

  const route = (envelope: SseEnvelope): void => {
    switch (envelope.event_type) {
      case "stream.degraded": {
        const reason =
          typeof envelope.payload.reason === "string"
            ? envelope.payload.reason
            : "upstream_disconnected";
        degrade(reason);
        return;
      }
      case "auth.expired":
        ended = true;
        handlers.onAuthExpired?.();
        controller.abort();
        return;
      case "run.snapshot":
      case "run.completed":
      case "run.failed":
      case "run.cancelled": {
        const snapshot = envelope.payload.snapshot;
        if (isRunSnapshot(snapshot)) {
          if (envelope.event_type === "run.snapshot") {
            handlers.onSnapshot?.(snapshot);
          } else {
            ended = true;
            handlers.onTerminal?.(snapshot);
            controller.abort(); // terminal: the Gateway closes after this event
          }
        }
        return;
      }
      default:
        break;
    }

    let deliver: SseEnvelope[];
    try {
      const result = sequencer.accept(envelope);
      if (result.duplicate) return;
      deliver = result.deliver;
    } catch (err) {
      if (err instanceof DegradeError) {
        degrade(err.reason);
        return;
      }
      throw err;
    }
    for (const event of deliver) {
      dispatchOnline(event);
    }
  };

  const dispatchOnline = (event: SseEnvelope): void => {
    switch (event.event_type) {
      case "message.delta": {
        const delta = event.payload.delta;
        if (event.message_id && typeof delta === "string") {
          handlers.onDelta?.(event.message_id, delta);
        }
        return;
      }
      case "run.progress": {
        const { phase, summary } = event.payload;
        if (typeof phase === "string" && typeof summary === "string") {
          handlers.onProgress?.({ phase, summary });
        }
        return;
      }
      case "run.started":
      case "run.status": {
        const status = event.payload.status;
        if (typeof status === "string" && isRunStatus(status)) {
          handlers.onStatus?.(status);
        }
        return;
      }
      default:
        // Unknown event_type: record and ignore (contract §12.2).
        return;
    }
  };

  const handleStreamFailure = (err: unknown): void => {
    if (signal.aborted || degraded || ended) return;
    if (err instanceof SseConnectError) {
      if (err.status === 401 || err.status === 403) {
        ended = true;
        handlers.onAuthExpired?.();
        return;
      }
      degrade("connect_failed");
      return;
    }
    degrade("upstream_disconnected");
  };

  const run = async (): Promise<void> => {
    try {
      stream = openSseStream(eventsUrl(runId), {
        signal,
        onFrame: handleFrame,
        fetchImpl: options.fetchImpl,
      });
    } catch (err) {
      handleStreamFailure(err);
      return;
    }
    try {
      await stream.done;
    } catch (err) {
      handleStreamFailure(err);
      return;
    }
    // The stream closed without a terminal/degraded decision (EOF after the
    // gateway dropped us): treat as a connection loss — recover by querying.
    if (!degraded && !ended) {
      degrade("upstream_disconnected");
    }
  };

  void run().then(doneResolve, doneReject);

  return {
    runId,
    get degraded() {
      return degraded;
    },
    close,
    done,
  };
}
