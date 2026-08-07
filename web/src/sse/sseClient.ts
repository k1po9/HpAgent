/**
 * Controllable SSE client built on fetch() + ReadableStream
 * (hpagent-web-api-contract.md §12.1).
 *
 * Native EventSource is deliberately NOT used: it auto-reconnects, re-sends
 * `Last-Event-ID`, and hides connect-phase JSON errors. This client exposes raw
 * SSE frames to the caller, closes cleanly via an AbortSignal, and throws a
 * typed error for connect failures so the caller can surface the JSON body.
 *
 * Frames are parsed incrementally with a streaming TextDecoder so multi-byte
 * UTF-8 deltas split across chunk boundaries are never corrupted.
 */

export interface SseFrame {
  /** The SSE `id:` field — equals the envelope event_id. */
  id: string | null;
  /** The SSE `event:` field — equals the envelope event_type. */
  event: string | null;
  /** The SSE `data:` field (multiple data lines joined with \n). */
  data: string;
}

export interface SseStreamOptions {
  /** Abort to close the stream cleanly (a controlled close, not an error). */
  signal?: AbortSignal;
  /** Called for every parsed SSE frame (including comment/keepalive frames). */
  onFrame: (frame: SseFrame) => void;
  /** fetch override for tests. */
  fetchImpl?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}

export interface SseStreamHandle {
  /** Resolves when the stream closes cleanly (EOF or abort); rejects on error. */
  done: Promise<void>;
}

/** The server failed the connection before any SSE frame (401/404/429/503 JSON). */
export class SseConnectError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    super(`SSE connect failed with HTTP ${status}`);
    this.name = "SseConnectError";
    this.status = status;
    this.body = body;
  }
}

/** Parse one blank-line-delimited SSE event block into a frame (null for a comment-only block). */
export function parseSseEventBlock(block: string): SseFrame | null {
  let id: string | null = null;
  let event: string | null = null;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // comment / keepalive
    if (line.startsWith("id:")) {
      id = line.slice(3).trimStart();
    } else if (line.startsWith("event:")) {
      event = line.slice(6).trimStart();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).trimStart());
    }
    // retry: and unknown fields are ignored (SSE spec).
  }
  if (id === null && event === null && data.length === 0) return null;
  return { id, event, data: data.join("\n") };
}

/** Split accumulated text into complete frames plus the unfinished remainder. */
export function extractSseFrames(text: string): { frames: SseFrame[]; rest: string } {
  const blocks = text.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  const frames: SseFrame[] = [];
  for (const block of blocks) {
    const frame = parseSseEventBlock(block);
    if (frame) frames.push(frame);
  }
  return { frames, rest };
}

export function openSseStream(url: string, options: SseStreamOptions): SseStreamHandle {
  const { signal, onFrame } = options;
  const fetchImpl =
    options.fetchImpl ??
    ((input: RequestInfo | URL, init?: RequestInit) => globalThis.fetch(input, init));

  const run = async (): Promise<void> => {
    let response: Response;
    try {
      response = await fetchImpl(url, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "text/event-stream" },
        signal,
      });
    } catch (err) {
      if (signal?.aborted) return;
      throw err;
    }
    if (!response.ok || !response.body) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        // Non-JSON connect error body: keep the null default.
      }
      throw new SseConnectError(response.status, body);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const dispatch = (frames: SseFrame[]): void => {
      for (const frame of frames) onFrame(frame);
    };
    // Aborting the fetch signal must also stop a pending body read: browsers
    // drop the connection, but undici/jsdom won't cancel the reader by itself.
    const onAbort = (): void => {
      void reader.cancel().catch(() => {});
    };
    if (signal?.aborted) {
      await reader.cancel().catch(() => {});
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = extractSseFrames(buffer);
        buffer = rest;
        dispatch(frames);
      }
      buffer += decoder.decode(); // flush trailing bytes
      const { frames, rest } = extractSseFrames(buffer);
      buffer = rest;
      dispatch(frames);
    } catch (err) {
      if (signal?.aborted) return;
      throw err;
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
  };

  return { done: run() };
}
