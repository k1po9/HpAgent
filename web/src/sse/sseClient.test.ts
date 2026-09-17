/**
 * SSE frame parser tests (contract §12.2 frame format).
 *
 * Covers: field parsing, blank-line splitting with a leftover remainder, CRLF
 * normalization, multi-byte UTF-8 split across chunk boundaries, typed connect
 * failures, and clean abort mid-stream.
 */
import { describe, expect, it } from "vitest";
import {
  extractSseFrames,
  openSseStream,
  parseSseEventBlock,
  SseConnectError,
  type SseFrame,
} from "./sseClient";

const ENCODER = new TextEncoder();

function streamResponse(chunks: Uint8Array[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function manualStream(): {
  response: Response;
  send: (text: string) => void;
  close: () => void;
} {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    send: (text) => controller.enqueue(ENCODER.encode(text)),
    close: () => controller.close(),
  };
}

const fetchWith = (response: Response) => async () => response;

describe("parseSseEventBlock", () => {
  it("parses id/event/data lines into a frame", () => {
    const frame = parseSseEventBlock('id: 1\nevent: run.started\ndata: {"a":1}');
    expect(frame).toEqual({ id: "1", event: "run.started", data: '{"a":1}' });
  });

  it("joins multiple data lines with a newline (SSE spec)", () => {
    const frame = parseSseEventBlock("id: 1\ndata: line1\ndata: line2");
    expect(frame?.data).toBe("line1\nline2");
  });

  it("returns null for a comment-only (keepalive) block", () => {
    expect(parseSseEventBlock(": keepalive")).toBeNull();
  });
});

describe("extractSseFrames", () => {
  it("splits blank-line-delimited frames and keeps the unfinished remainder", () => {
    const { frames, rest } = extractSseFrames(
      "id: 1\nevent: a\ndata: x\n\nid: 2\nevent: b\ndata: y\n\nid: 3\neve",
    );
    expect(frames.map((f) => f.id)).toEqual(["1", "2"]);
    expect(rest).toBe("id: 3\neve");
  });

  it("normalizes CRLF line endings", () => {
    const { frames } = extractSseFrames("id: 1\r\nevent: a\r\ndata: x\r\n\r\n");
    expect(frames).toHaveLength(1);
    expect(frames[0]).toEqual({ id: "1", event: "a", data: "x" });
  });
});

describe("openSseStream", () => {
  it("delivers frames split across chunk boundaries without corrupting UTF-8", async () => {
    const frames: SseFrame[] = [];
    const body = 'id: 1\nevent: message.delta\ndata: {"delta":"你好"}\n\n';
    const bytes = ENCODER.encode(body);
    // Split one byte into the multi-byte "你" (0xE4 0xBD 0xA0).
    const cut = bytes.indexOf(0xe4) + 1;
    const response = streamResponse([bytes.slice(0, cut), bytes.slice(cut)]);

    const handle = openSseStream("/api/v1/runs/r1/events", {
      onFrame: (f) => frames.push(f),
      fetchImpl: fetchWith(response),
    });
    await handle.done;
    expect(frames).toHaveLength(1);
    const parsed = JSON.parse(frames[0]!.data) as { delta: string };
    expect(parsed.delta).toBe("你好");
  });

  it("delivers multiple frames from one chunk", async () => {
    const frames: SseFrame[] = [];
    const body =
      'id: 1\nevent: run.started\ndata: {}\n\nid: 2\nevent: message.delta\ndata: {"delta":"a"}\n\n';
    const handle = openSseStream("/events", {
      onFrame: (f) => frames.push(f),
      fetchImpl: fetchWith(streamResponse([ENCODER.encode(body)])),
    });
    await handle.done;
    expect(frames.map((f) => f.id)).toEqual(["1", "2"]);
    expect(frames.map((f) => f.event)).toEqual(["run.started", "message.delta"]);
  });

  it("throws a typed error for a JSON connect failure with the body attached", async () => {
    const response = new Response(
      JSON.stringify({ error: { code: "resource_not_found", message: "no such run" } }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
    const handle = openSseStream("/events", {
      onFrame: () => {},
      fetchImpl: fetchWith(response),
    });
    const err = await handle.done.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(SseConnectError);
    const connectError = err as SseConnectError;
    expect(connectError.status).toBe(404);
    expect((connectError.body as { error: { code: string } }).error.code).toBe(
      "resource_not_found",
    );
  });

  it("resolves done cleanly when aborted mid-stream", async () => {
    const stream = manualStream();
    const controller = new AbortController();
    const frames: SseFrame[] = [];
    const handle = openSseStream("/events", {
      signal: controller.signal,
      onFrame: (f) => frames.push(f),
      fetchImpl: fetchWith(stream.response),
    });
    stream.send("id: 1\nevent: run.started\ndata: {}\n\n");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(frames).toHaveLength(1);
    controller.abort();
    await expect(handle.done).resolves.toBeUndefined();
  });
});
