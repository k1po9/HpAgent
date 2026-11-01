import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { RestoreIdentity } from "./RestoreIdentity";
import type { SceneStatus } from "../identity/IdentityScene";

let scene: SceneStatus;
vi.mock("../identity/IdentityScene", () => ({
  IdentityScene: ({ onStatus }: { onStatus: (status: SceneStatus) => void }) => {
    useEffect(() => onStatus(scene), [onStatus]);
    return null;
  },
}));

beforeEach(() => {
  vi.useFakeTimers();
  scene = "ready";
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("only enters after a verified session and a short scene presentation", async () => {
  const onReady = vi.fn();
  const view = render(<RestoreIdentity verified={false} confirmed={false} onReady={onReady} />);
  expect(screen.getByRole("status")).toHaveTextContent("正在恢复会话");
  expect(screen.queryByLabelText("密码")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "跳过特效" })).toBeDisabled();
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(onReady).not.toHaveBeenCalled();
  view.rerender(<RestoreIdentity verified confirmed={false} onReady={onReady} username="Alice" />);
  expect(screen.getByRole("status")).toHaveTextContent("会话已恢复");
  await act(() => vi.advanceTimersByTimeAsync(1800));
  expect(onReady).toHaveBeenCalledTimes(1);
});

it("allows skipping after verification and cleans up pending timers", async () => {
  const onReady = vi.fn();
  const view = render(<RestoreIdentity verified confirmed={false} onReady={onReady} />);
  fireEvent.click(screen.getByRole("button", { name: "跳过特效" }));
  expect(onReady).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(onReady).toHaveBeenCalledTimes(1);
});

it("never strands a verified session when the scene chunk stalls", async () => {
  scene = "loading";
  const onReady = vi.fn();
  render(<RestoreIdentity verified confirmed={false} onReady={onReady} />);
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(onReady).toHaveBeenCalledTimes(1);
});

it("enters immediately if WebGL is unavailable", () => {
  scene = "fallback";
  const onReady = vi.fn();
  render(<RestoreIdentity verified confirmed={false} onReady={onReady} />);
  expect(onReady).toHaveBeenCalledTimes(1);
});

it("settles a running presentation when the page becomes hidden", () => {
  const onReady = vi.fn();
  render(<RestoreIdentity verified confirmed={false} onReady={onReady} />);
  act(() => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(onReady).toHaveBeenCalledTimes(1);
});

it("skips presentation delays under reduced motion", () => {
  const media = window.matchMedia;
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    ...media(query),
    matches: query === "(prefers-reduced-motion: reduce)",
  }));
  const onReady = vi.fn();
  render(<RestoreIdentity verified confirmed={false} onReady={onReady} />);
  expect(onReady).toHaveBeenCalledTimes(1);
});
