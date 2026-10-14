import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VitalityHeading } from "./VitalitySignal";
import { deriveVitality, type VitalityState } from "./policy";
import { setMotionPreference } from "../motion/preference";
import { useAuth } from "../../store/auth";
let intersect: (e: { isIntersecting: boolean }[]) => void;
const disconnect = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  setMotionPreference("system");
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(fn: typeof intersect) {
        intersect = fn;
      }
      observe() {}
      disconnect = disconnect;
    },
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  act(() => setMotionPreference("system"));
});
function heading(state: VitalityState, runId = "R", generation = 7) {
  const view = {
    ...deriveVitality({ run_id: runId, status: "running" }, { now: 0 }),
    state,
    terminal: ["succeeded", "failed", "cancelled"].includes(state),
  };
  return <VitalityHeading view={view} runId={runId} generation={generation} />;
}
it("100ms quick switches retain stable frame, at most two layers, and latest wins", () => {
  const { container, rerender, unmount } = render(heading("running"));
  const frame = container.querySelector(".hp-vitality__frame");
  for (const state of ["thinking", "tool", "waiting", "thinking", "failed"] as const) {
    act(() => vi.advanceTimersByTime(100));
    rerender(heading(state));
    expect(container.querySelector(".hp-vitality__frame")).toBe(frame);
    expect(container.querySelectorAll(".hp-vitality__layer").length).toBeLessThanOrEqual(2);
    expect(container.querySelector(".hp-vitality")).toHaveAttribute("data-vitality", state);
  }
  act(() => vi.advanceTimersByTime(620));
  expect(container.querySelectorAll(".hp-vitality__layer")).toHaveLength(1);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
  expect(disconnect).toHaveBeenCalled();
});
it("history and repeated terminal are static; an observed terminal celebrates once", () => {
  const { container, rerender } = render(heading("succeeded"));
  const signal = () => container.querySelector(".hp-vitality");
  expect(signal()).toHaveAttribute("data-celebrate", "false");
  rerender(heading("succeeded"));
  expect(signal()).toHaveAttribute("data-celebrate", "false");
  rerender(heading("running", "NEW"));
  rerender(heading("succeeded", "NEW"));
  expect(signal()).toHaveAttribute("data-celebrate", "true");
  act(() => vi.advanceTimersByTime(620));
  rerender(heading("succeeded", "NEW"));
  expect(signal()).toHaveAttribute("data-celebrate", "false");
});
it.each(["reduce", "hidden", "offscreen"])(
  "%s interrupts immediately and does not replay",
  (kind) => {
    const schedule = vi.spyOn(window, "setTimeout");
    const clear = vi.spyOn(window, "clearTimeout");
    const { container, rerender } = render(heading("running"));
    rerender(heading("failed"));
    const relayTimer = schedule.mock.results.at(-1)?.value;
    act(() => {
      if (kind === "reduce") setMotionPreference("reduce");
      if (kind === "hidden") {
        vi.spyOn(document, "hidden", "get").mockReturnValue(true);
        document.dispatchEvent(new Event("visibilitychange"));
      }
      if (kind === "offscreen") intersect([{ isIntersecting: false }]);
    });
    expect(container.querySelector(".hp-vitality")).toHaveAttribute("data-animate", "false");
    expect(container.querySelectorAll(".hp-vitality__layer")).toHaveLength(1);
    expect(clear).toHaveBeenCalledWith(relayTimer);
    act(() => {
      vi.restoreAllMocks();
      document.dispatchEvent(new Event("visibilitychange"));
      setMotionPreference("system");
      intersect([{ isIntersecting: true }]);
    });
    expect(container.querySelector(".hp-vitality")).toHaveAttribute("data-celebrate", "false");
  },
);
it("Run, subscription and account identities dispose old relay", () => {
  const { container, rerender } = render(heading("running"));
  for (const [id, gen] of [
    ["NEW", 7],
    ["NEW", 8],
  ] as const) {
    rerender(heading("thinking"));
    rerender(heading("failed", id, gen));
    expect(container.querySelectorAll(".hp-vitality__layer")).toHaveLength(1);
    expect(container.querySelector(".hp-vitality")).toHaveAttribute("data-celebrate", "false");
  }
  rerender(heading("thinking", "NEW", 8));
  act(() =>
    useAuth.setState({
      account: { account_id: "other" } as NonNullable<
        ReturnType<typeof useAuth.getState>["account"]
      >,
    }),
  );
  expect(container.querySelectorAll(".hp-vitality__layer")).toHaveLength(1);
  expect(vi.getTimerCount()).toBe(0);
});
