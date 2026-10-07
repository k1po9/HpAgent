import { workFixture } from "../components/tasks/taskFixtures";
import { HpCommandError } from "../api/types";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { useWorks } from "./works";
import type { HpWorkEvent } from "../api/types";

afterEach(() => {
  useWorks.getState().reset();
  vi.restoreAllMocks();
});

it("deduplicates recovered business events with an independent Work cursor", () => {
  const refresh = vi.fn(async () => undefined);
  useWorks.setState({ items: [workFixture()] });
  const originalRefresh = useWorks.getState().refresh;
  const event: HpWorkEvent = {
    event_id: "e1",
    work_id: "w1",
    event_seq: 5,
    event_type: "completed",
    requirement_revision: 2,
    bounded_payload: {},
  };
  const spy = vi.spyOn(useWorks.getState(), "refresh").mockImplementation(refresh);
  useWorks.getState().receive(event);
  useWorks.getState().receive(event);
  useWorks.getState().receive({ ...event, event_seq: 4 });
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(useWorks.getState().events.w1).toEqual([event]);
  expect(useWorks.getState().cursors.w1).toBe(5);
  spy.mockRestore();
  useWorks.setState({ refresh: originalRefresh });
});

it("ignores an account snapshot that arrives after sign-out", async () => {
  let resolve!: (value: unknown) => void;
  vi.spyOn(api, "request").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }) as never,
  );
  const pending = useWorks.getState().load();
  useWorks.getState().reset();
  resolve({ items: [{ work_id: "private-old-account" }] });
  await pending;
  expect(useWorks.getState().items).toEqual([]);
});

it("scans 101 tasks serially, preserves later pages and monotonic detail upserts", async () => {
  const make = (index: number) => workFixture({ work_id: `w${index}` });
  const spy = vi
    .spyOn(api, "request")
    .mockResolvedValueOnce({
      items: Array.from({ length: 50 }, (_, i) => make(i)),
      next_before: "page-2",
    })
    .mockResolvedValueOnce({
      items: Array.from({ length: 50 }, (_, i) => make(i + 50)),
      next_before: "page-3",
    })
    .mockResolvedValueOnce({ items: [make(100)], next_before: null });
  await useWorks.getState().load();
  expect(useWorks.getState().items).toHaveLength(101);
  expect(useWorks.getState().loadState).toBe("complete");
  expect(spy.mock.calls[1]![0].path).toBe("/api/v1/works?before=page-2");
  useWorks.getState().upsert({ ...make(100), row_version: 5 });
  spy.mockResolvedValueOnce({ items: [make(0)], next_before: null });
  await useWorks.getState().load();
  expect(useWorks.getState().items).toHaveLength(101);
  spy.mockResolvedValueOnce({ work: make(100) });
  await useWorks.getState().refresh("w100");
  expect(useWorks.getState().items.find((w) => w.work_id === "w100")?.row_version).toBe(5);
  spy.mockResolvedValueOnce({ work: make(200) });
  await useWorks.getState().refresh("w200");
  expect(useWorks.getState().items).toHaveLength(102);
});
it("retains failed-page cursor, resumes and rejects repeated cursors", async () => {
  const spy = vi
    .spyOn(api, "request")
    .mockResolvedValueOnce({ items: [workFixture()], next_before: "next" })
    .mockRejectedValueOnce(new Error("offline"));
  await useWorks.getState().load();
  expect(useWorks.getState().loadState).toBe("partial");
  expect(useWorks.getState().nextBefore).toBe("next");
  spy.mockResolvedValueOnce({ items: [workFixture({ work_id: "w2" })], next_before: null });
  await useWorks.getState().load(true);
  expect(spy.mock.calls[2]![0].path).toBe("/api/v1/works?before=next");
  expect(useWorks.getState().loadState).toBe("complete");
  spy
    .mockResolvedValueOnce({ items: [workFixture()], next_before: "same" })
    .mockResolvedValueOnce({ items: [workFixture({ work_id: "w3" })], next_before: "same" });
  await useWorks.getState().load();
  expect(useWorks.getState().loadState).toBe("partial");
  expect(useWorks.getState().error).toContain("未前进");
});
it("joins concurrent scan and detail requests and ignores old-account response", async () => {
  let done!: (v: unknown) => void;
  const spy = vi.spyOn(api, "request").mockImplementation(
    () =>
      new Promise((r) => {
        done = r;
      }) as never,
  );
  const scan = useWorks.getState().load(),
    same = useWorks.getState().load();
  expect(scan).toBe(same);
  expect(spy).toHaveBeenCalledTimes(1);
  done({ items: [], next_before: null });
  await scan;
  const detail = useWorks.getState().refresh("deep"),
    also = useWorks.getState().refresh("deep");
  expect(detail).toBe(also);
  useWorks.getState().reset();
  done({ work: workFixture({ work_id: "deep" }) });
  await detail;
  expect(useWorks.getState().items).toEqual([]);
});
it("removes unavailable objects and preserves cached snapshot on network failure", async () => {
  useWorks.getState().upsert(workFixture());
  const spy = vi.spyOn(api, "request").mockRejectedValueOnce(new Error("offline"));
  await expect(useWorks.getState().refresh("w1")).rejects.toThrow();
  expect(useWorks.getState().items).toHaveLength(1);
  spy.mockRejectedValueOnce(
    new HpCommandError(404, {
      code: "resource_not_found",
      message: "missing",
      request_id: null,
      retryable: false,
      details: {},
    }),
  );
  await expect(useWorks.getState().refresh("w1")).rejects.toThrow();
  expect(useWorks.getState().items).toEqual([]);
  expect(useWorks.getState().detailErrors.w1).toBe("对象不可用。");
});
it("submits only explicitly raised budget dimensions", async () => {
  const spy = vi.spyOn(api, "request").mockResolvedValue({ work: workFixture() });
  expect(
    await useWorks.getState().increaseBudget(workFixture(), { model_total_tokens: 1100 }),
  ).toBe(true);
  expect(spy.mock.calls[0]![0].body).toEqual({
    budget_version: 1,
    limits: { model_total_tokens: 1100 },
  });
  expect(await useWorks.getState().increaseBudget(workFixture(), { model_total_tokens: 999 })).toBe(
    false,
  );
  expect(await useWorks.getState().increaseBudget(workFixture(), { unknown: 1100 })).toBe(false);
  expect(spy).toHaveBeenCalledTimes(1);
});
