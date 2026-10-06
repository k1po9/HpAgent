import { afterEach, describe, expect, it, vi } from "vitest";
import { openSseStream } from "./sseClient";
import { disposeWorkFeeds, subscribeWork } from "./workFeed";

vi.mock("./sseClient", () => ({ openSseStream: vi.fn() }));
afterEach(() => {
  disposeWorkFeeds();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe("UI-1 Work feed lifecycle", () => {
  it("keeps one connection until it finishes and clears reconnect waits on disposal", async () => {
    vi.useFakeTimers();
    let finish!: () => void;
    const done = new Promise<void>((resolve) => {
      finish = resolve;
    });
    vi.mocked(openSseStream).mockReturnValue({ done });
    const recover = vi.fn();
    const receive = vi.fn();
    subscribeWork("w", () => 0, receive, recover);
    await vi.advanceTimersByTimeAsync(12000);
    expect(openSseStream).toHaveBeenCalledOnce();
    const options = vi.mocked(openSseStream).mock.calls[0]![1];
    finish();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(1);
    disposeWorkFeeds();
    expect(options.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    options.onFrame({
      id: "late",
      event: "work.event",
      data: JSON.stringify({ work_id: "w", event_seq: 1 }),
    });
    await vi.advanceTimersByTimeAsync(12000);
    expect(receive).not.toHaveBeenCalled();
    expect(openSseStream).toHaveBeenCalledOnce();
  });

  it("recovers a failed connection once and stops before reconnecting after logout", async () => {
    vi.useFakeTimers();
    vi.mocked(openSseStream).mockReturnValue({ done: Promise.reject(new Error("offline")) });
    const recover = vi.fn(async () => {});
    subscribeWork("w", () => 0, vi.fn(), recover);
    await vi.advanceTimersByTimeAsync(0);
    expect(recover).toHaveBeenCalledOnce();
    disposeWorkFeeds();
    await vi.advanceTimersByTimeAsync(12000);
    expect(openSseStream).toHaveBeenCalledOnce();
  });
});
