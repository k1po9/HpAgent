import { act, render, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setMotionPreference } from "../motion/preference";
import { IdentityScene, type IdentitySceneHandle, type SceneStatus } from "./IdentityScene";

const stages = vi.hoisted(() => [] as StageDouble[]);
interface StageDouble {
  targets: Record<string, number>;
  onFatal: () => void;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
  renderOnce: ReturnType<typeof vi.fn>;
  pulse: ReturnType<typeof vi.fn>;
  playDissolve: ReturnType<typeof vi.fn>;
}

vi.mock("./scene/IdentityStage", () => ({
  IdentityStage: class {
    targets = {};
    start = vi.fn();
    stop = vi.fn();
    dispose = vi.fn();
    renderOnce = vi.fn();
    pulse = vi.fn();
    playAssemble = vi.fn();
    setAssembled = vi.fn();
    playDissolve = vi.fn();
    setLayout = vi.fn();
    project = () => ({ x: 0, y: 0 });
    constructor(_canvas: unknown, _quality: unknown, _base: unknown, onFatal: () => void) {
      (this as unknown as StageDouble).onFatal = onFatal;
      stages.push(this as unknown as StageDouble);
    }
  },
}));

function mount(onStatus: (status: SceneStatus) => void = () => {}, phase = "idle" as const) {
  const handle = createRef<IdentitySceneHandle>();
  const panel = { current: null };
  const view = (next: "idle" | "verifying" | "confirmed" = phase) => (
    <IdentityScene
      ref={handle}
      phase={next}
      rejection={0}
      panel={panel}
      onStatus={onStatus}
      onDenied={() => {}}
    />
  );
  return { handle, view, ...render(view()) };
}

describe("IdentityScene", () => {
  beforeEach(() => {
    stages.length = 0;
    setMotionPreference("system");
    vi.stubGlobal("WebGL2RenderingContext", class {});
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      getExtension: () => null,
    } as unknown as RenderingContext);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setMotionPreference("system");
  });

  it("never builds a stage when unmounted before the lazy chunk resolves", async () => {
    const { unmount } = mount();
    unmount();
    await act(async () => {
      await import("./scene/IdentityStage");
      await new Promise((resolve) => setTimeout(resolve));
    });
    expect(stages).toHaveLength(0);
  });

  it("runs while motion is allowed and releases everything on unmount", async () => {
    const { unmount } = mount();
    await waitFor(() => expect(stages[0]?.start).toHaveBeenCalled());
    unmount();
    expect(stages[0]!.dispose).toHaveBeenCalledTimes(1);
  });

  it("draws one frame and holds no loop under the reduced preference", async () => {
    setMotionPreference("reduce");
    const onStatus = vi.fn();
    mount(onStatus);
    await waitFor(() => expect(stages[0]?.renderOnce).toHaveBeenCalled());
    expect(stages[0]!.start).not.toHaveBeenCalled();
    expect(onStatus).toHaveBeenCalledWith("ready");
  });

  it("releases the stage and reports fallback on a fatal load", async () => {
    const onStatus = vi.fn();
    mount(onStatus);
    await waitFor(() => expect(stages[0]).toBeDefined());
    act(() => stages[0]!.onFatal());
    expect(stages[0]!.dispose).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenCalledWith("fallback");
  });

  it("forwards input pulses and dissolves only on confirmation", async () => {
    const { handle, view, rerender } = mount();
    await waitFor(() => expect(stages[0]?.start).toHaveBeenCalled());
    handle.current!.pulse();
    expect(stages[0]!.pulse).toHaveBeenCalledTimes(1);
    rerender(view("verifying"));
    expect(stages[0]!.playDissolve).not.toHaveBeenCalled();
    rerender(view("confirmed"));
    expect(stages[0]!.playDissolve).toHaveBeenCalledTimes(1);
  });
});
