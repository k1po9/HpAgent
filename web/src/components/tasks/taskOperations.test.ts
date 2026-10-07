import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { HpCommandError } from "../../api/types";
import {
  taskCommand,
  resetTaskOperations,
  useTaskOperations,
  acquireTaskLock,
  reserveTaskIntent,
} from "./taskOperations";
afterEach(() => {
  resetTaskOperations();
  vi.restoreAllMocks();
});
const request = {
  method: "POST" as const,
  path: "/api/v1/works/w/pause",
  body: {},
  headers: { "If-Match": '"work-w-v1"' },
};
it("locks double click and reuses original key/version after uncertain response", async () => {
  const spy = vi
    .spyOn(api, "request")
    .mockRejectedValueOnce(new TypeError("lost response"))
    .mockResolvedValue({});
  await taskCommand("w", request, async () => {});
  const first = spy.mock.calls[0]![0];
  await taskCommand("w", { ...request, headers: { "If-Match": '"work-w-v9"' } }, async () => {});
  expect(spy.mock.calls[1]![0].idempotencyKey).toBe(first.idempotencyKey);
  expect(spy.mock.calls[1]![0].headers).toEqual(first.headers);
  await taskCommand("w", request, async () => {});
  expect(spy.mock.calls[2]![0].idempotencyKey).not.toBe(first.idempotencyKey);
});
it("prevents overlapping same Work but permits different Works", async () => {
  let done!: (v: unknown) => void;
  const spy = vi
    .spyOn(api, "request")
    .mockImplementationOnce(
      () =>
        new Promise((r) => {
          done = r;
        }) as never,
    )
    .mockResolvedValue({});
  const first = taskCommand("w", request, async () => {});
  expect(await taskCommand("w", request, async () => {})).toBe(false);
  expect(await taskCommand("other", request, async () => {})).toBe(true);
  expect(spy).toHaveBeenCalledTimes(2);
  done({});
  await first;
});
it("does not resend a committed mutation when recovery GET fails", async () => {
  const spy = vi.spyOn(api, "request").mockResolvedValue({});
  expect(
    await taskCommand("w", request, async () => {
      throw new Error("GET failed");
    }),
  ).toBe(true);
  expect(spy).toHaveBeenCalledTimes(1);
  expect(useTaskOperations.getState().intents.w?.notice).toContain("操作已提交，状态同步失败");
  expect(useTaskOperations.getState().intents.w?.uncertain).toBe(false);
});
it("does not replace uncertain payload and does not apply old-account response", async () => {
  const spy = vi.spyOn(api, "request").mockRejectedValue(new TypeError("lost"));
  await taskCommand("w", request, async () => {});
  expect(await taskCommand("w", { ...request, body: { changed: true } }, async () => {})).toBe(
    false,
  );
  expect(spy).toHaveBeenCalledTimes(1);
  resetTaskOperations();
  let done!: (v: unknown) => void;
  spy.mockImplementationOnce(
    () =>
      new Promise((r) => {
        done = r;
      }) as never,
  );
  const recover = vi.fn(async () => {}),
    pending = taskCommand("w", request, recover);
  resetTaskOperations();
  done({});
  await pending;
  expect(recover).not.toHaveBeenCalled();
  expect(useTaskOperations.getState().intents).toEqual({});
});
it("definitive conflict refreshes and explicit reviewed submit receives a new key", async () => {
  const conflict = new HpCommandError(409, {
    code: "version_conflict",
    message: "conflict",
    request_id: "r",
    retryable: false,
    details: {},
  });
  const spy = vi.spyOn(api, "request").mockRejectedValueOnce(conflict).mockResolvedValue({});
  const recover = vi.fn(async () => {});
  await taskCommand("w", request, recover);
  expect(recover).toHaveBeenCalled();
  await taskCommand("w", { ...request, headers: { "If-Match": '"work-w-v2"' } }, recover);
  expect(spy.mock.calls[1]![0].idempotencyKey).not.toBe(spy.mock.calls[0]![0].idempotencyKey);
});

it("publishes immutable command progress so selectors observe busy/error completion", async () => {
  vi.spyOn(api, "request").mockRejectedValue(new TypeError("lost"));
  const seen: unknown[] = [];
  const stop = useTaskOperations.subscribe((s) => seen.push(s.intents.w));
  await taskCommand("w", request, async () => {});
  stop();
  expect(seen).toHaveLength(3);
  expect(seen[0]).not.toBe(seen[1]);
  expect(seen[1]).not.toBe(seen[2]);
  expect(useTaskOperations.getState().intents.w?.busy).toBe(false);
  expect(useTaskOperations.getState().intents.w?.uncertain).toBe(true);
});

it("shares Work locks and unknown reservations with resource commands", async () => {
  const spy = vi.spyOn(api, "request").mockResolvedValue({});
  const release = acquireTaskLock("w", "resource-request");
  expect(await taskCommand("w", request, async () => {})).toBe(false);
  expect(spy).not.toHaveBeenCalled();
  release();
  reserveTaskIntent("w", "resource-request", true);
  expect(await taskCommand("w", request, async () => {})).toBe(false);
  expect(() => acquireTaskLock("w", "different-request")).toThrow();
  acquireTaskLock("w", "resource-request")();
  reserveTaskIntent("w", "resource-request", false);
  expect(await taskCommand("w", request, async () => {})).toBe(true);
});
