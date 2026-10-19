import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunTimeline } from "./RunTimeline";
import type { HpRunStatus } from "../../api/types";
const run = {
  run_id: "timing",
  status: "succeeded" as HpRunStatus,
  created_at: "2026-10-18T15:59:40Z",
  started_at: "2026-10-18T15:59:43Z",
  finished_at: "2026-10-18T16:01:12Z",
};
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("computes waiting and execution across midnight from independent timestamps", () => {
  render(<RunTimeline run={run} />);
  expect(screen.getByText("等待 3秒")).toBeVisible();
  expect(screen.getByText("执行 1分 29秒")).toBeVisible();
  expect(document.querySelectorAll("time")).toHaveLength(3);
});
it.each([null, "invalid", "2026-10-18T15:00:00Z", "2026-10-18T17:00:00Z"])(
  "does not infer elapsed execution from absent or reversed start %s",
  (started_at) => {
    render(<RunTimeline run={{ ...run, started_at }} />);
    expect(screen.queryByText(/^执行 \d/)).toBeNull();
  },
);
it("keeps real zero duration", () => {
  render(<RunTimeline run={{ ...run, started_at: run.created_at, finished_at: run.created_at }} />);
  expect(screen.getByText("等待 0秒")).toBeVisible();
  expect(screen.getByText("执行 0秒")).toBeVisible();
});
it("updates running elapsed time, freezes on finish, and clears its timer", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-18T16:01:12Z"));
  const view = render(<RunTimeline run={{ ...run, status: "running", finished_at: null }} />);
  expect(screen.getByText("已执行 1分 29秒")).toBeVisible();
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByText("已执行 1分 31秒")).toBeVisible();
  view.rerender(<RunTimeline run={run} />);
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(5000));
  expect(screen.getByText("执行 1分 29秒")).toBeVisible();
});
it("shows waiting elapsed only for a real queued state", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-18T16:01:12Z"));
  const view = render(
    <RunTimeline run={{ ...run, status: "queued", started_at: null, finished_at: null }} />,
  );
  expect(screen.getByText("等待中 1分 32秒")).toBeVisible();
  expect(screen.getByText("尚未开始")).toBeVisible();
  view.rerender(
    <RunTimeline run={{ ...run, status: "running", started_at: null, finished_at: null }} />,
  );
  expect(screen.queryByText(/^等待中/)).toBeNull();
  expect(screen.queryByText("尚未开始")).toBeNull();
});
it.each(["failed", "cancelled"] as const)(
  "labels the real terminal outcome %s without making up a start",
  (status) => {
    render(<RunTimeline run={{ ...run, status, started_at: null }} />);
    expect(screen.getByText(status === "failed" ? "失败" : "取消")).toBeVisible();
    expect(screen.queryByText(/^执行 \d/)).toBeNull();
    expect(screen.getByText("时间未提供")).toBeVisible();
  },
);
