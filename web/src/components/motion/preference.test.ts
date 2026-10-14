import { afterEach, expect, it, vi } from "vitest";
import { animateSpatial, motionAllowed, setMotionPreference } from "./preference";
afterEach(() => {
  setMotionPreference("system");
  localStorage.clear();
  vi.restoreAllMocks();
});
it("system reduction wins over local system, and hidden does not allow motion", () => {
  setMotionPreference("system");
  vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
  expect(motionAllowed()).toBe(false);
  vi.restoreAllMocks();
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  expect(motionAllowed()).toBe(false);
});
it.each(["reduce", "hidden", "resize"])(
  "cancels a live effect on %s and disposes once",
  (cause) => {
    let finish!: () => void;
    const cancel = vi.fn(),
      done = vi.fn();
    const node = document.createElement("div");
    node.animate = vi.fn(
      () =>
        ({
          cancel,
          finished: new Promise<void>((r) => {
            finish = r;
          }),
        }) as unknown as Animation,
    );
    const dispose = animateSpatial(node, [{ opacity: 0.5 }, { opacity: 1 }], 300, "ease", done);
    if (cause === "reduce") setMotionPreference("reduce");
    if (cause === "hidden") {
      vi.spyOn(document, "hidden", "get").mockReturnValue(true);
      document.dispatchEvent(new Event("visibilitychange"));
    }
    if (cause === "resize") window.dispatchEvent(new Event("resize"));
    dispose();
    finish();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledTimes(1);
  },
);
it("a reduced local preference performs no animation or deferred completion", () => {
  setMotionPreference("reduce");
  const node = document.createElement("div");
  node.animate = vi.fn();
  const done = vi.fn();
  animateSpatial(node, [], 300, "ease", done)();
  expect(node.animate).not.toHaveBeenCalled();
  expect(done).toHaveBeenCalledTimes(1);
});
