import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { useWorks } from "./works";
import type { HpWork, HpWorkEvent } from "../api/types";

afterEach(() => {
  useWorks.getState().reset();
  vi.restoreAllMocks();
});

it("deduplicates recovered business events with an independent Work cursor", () => {
  const refresh = vi.fn(async () => undefined);
  useWorks.setState({ items: [{ work_id: "w1" } as HpWork] });
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
