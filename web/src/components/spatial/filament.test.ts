import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { playFilament } from "./filament";
import { setMotionPreference } from "../motion/preference";
let source: HTMLButtonElement, target: HTMLDivElement;
let frames: Map<number, FrameRequestCallback>, next: number, clock: number;
beforeEach(() => {
  setMotionPreference("system");
  vi.stubGlobal("innerWidth", 1680);
  vi.stubGlobal("innerHeight", 1000);
  frames = new Map();
  next = 0;
  clock = 0;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((fn: FrameRequestCallback) => {
      frames.set(++next, fn);
      return next;
    }),
  );
  vi.stubGlobal(
    "cancelAnimationFrame",
    vi.fn((id: number) => frames.delete(id)),
  );
  Object.defineProperty(SVGElement.prototype, "getTotalLength", {
    configurable: true,
    value: () => 100,
  });
  Object.defineProperty(SVGElement.prototype, "getPointAtLength", {
    configurable: true,
    value: (length: number) => ({ x: length, y: 100 }),
  });
  source = document.createElement("button");
  target = document.createElement("div");
  document.body.append(source, target);
  source.getBoundingClientRect = () => new DOMRect(400, 100, 200, 44);
  target.getBoundingClientRect = () => new DOMRect(1290, 80, 390, 68);
});
afterEach(() => {
  source.remove();
  target.remove();
  document.querySelectorAll(".hp-inspector-relay").forEach((n) => n.remove());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function tick(t: number) {
  clock = t;
  const [id, fn] = [...frames].at(-1)!;
  frames.delete(id);
  fn(t);
}
function depart() {
  tick(16);
  tick(80);
}
it.each([false, true])(
  "allows the reported fractional edge error on arrival/return (%s)",
  (closing) => {
    target.getBoundingClientRect = () => new DOMRect(1290.666748046875, 78, 389.3333435058594, 68);
    const arrive = vi.fn();
    const dispose = playFilament({
      source,
      target,
      token: 4,
      current: () => true,
      arrive,
      settle: vi.fn(),
      closing,
    });
    if (!closing) depart();
    expect(document.querySelector(".hp-inspector-relay")).not.toBeNull();
    expect(arrive).not.toHaveBeenCalled();
    dispose();
  },
);
it.each([false, true])(
  "still rejects an actual offscreen edge on arrival/return (%s)",
  (closing) => {
    target.getBoundingClientRect = () => new DOMRect(1290, 78, 390.02, 68);
    const arrive = vi.fn(),
      settle = vi.fn();
    playFilament({ source, target, token: 4, current: () => true, arrive, settle, closing });
    if (!closing) tick(16);
    expect(document.querySelector(".hp-inspector-relay")).toBeNull();
    expect(arrive).toHaveBeenCalledTimes(1);
    expect(settle).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
  },
);
it("arrives from the actual measured SVG tip, then releases every local resource", () => {
  const arrive = vi.fn(),
    settle = vi.fn();
  playFilament({ source, target, token: 1, current: () => true, arrive, settle });
  depart();
  for (let t = 180; t <= 680; t += 100) tick(t);
  expect(arrive).not.toHaveBeenCalled();
  expect(
    Number(document.querySelector<SVGSVGElement>(".hp-inspector-relay")!.dataset.tipLength),
  ).toBeLessThan(99.5);
  tick(760);
  expect(arrive).toHaveBeenCalledTimes(1);
  tick(860);
  expect(settle).toHaveBeenCalledTimes(1);
  expect(frames.size).toBe(0);
  expect(document.querySelector(".hp-inspector-relay")).toBeNull();
});
it.each(["long-frame", "scroll", "reduce", "unmount", "geometry", "modal"])(
  "%s converges instead of sticking in receiving",
  (kind) => {
    const arrive = vi.fn(),
      settle = vi.fn();
    playFilament({ source, target, token: 1, current: () => true, arrive, settle });
    depart();
    if (kind === "scroll") window.dispatchEvent(new Event("scroll"));
    if (kind === "reduce") setMotionPreference("reduce");
    if (kind === "unmount") source.remove();
    if (kind === "geometry") source.getBoundingClientRect = () => new DOMRect(500, 100, 200, 44);
    const modal = document.createElement("dialog");
    if (kind === "modal") {
      modal.setAttribute("open", "");
      document.body.append(modal);
    }
    if (frames.size) tick(kind === "long-frame" ? 330 : 96);
    expect(arrive).toHaveBeenCalledTimes(1);
    expect(settle).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
    expect(document.querySelector(".hp-inspector-relay")).toBeNull();
    modal.remove();
  },
);
it("cancelled and stale frames can never reveal, even if an old callback is invoked again", () => {
  let current = true;
  const arrive = vi.fn(),
    settle = vi.fn();
  const dispose = playFilament({
    source,
    target,
    token: 1,
    current: () => current,
    arrive,
    settle,
  });
  const late = [...frames.values()][0]!;
  current = false;
  dispose();
  late(980);
  expect(arrive).not.toHaveBeenCalled();
  expect(settle).not.toHaveBeenCalled();
  expect(frames.size).toBe(0);
});
it("return can use an inert empty shell without enabling its interaction", () => {
  target.inert = true;
  target.setAttribute("inert", "");
  const dispose = playFilament({
    source,
    target,
    token: 2,
    current: () => true,
    closing: true,
    arrive: vi.fn(),
    settle: vi.fn(),
  });
  expect(document.querySelector(".hp-inspector-relay")).toHaveAttribute("data-direction", "return");
  expect(target).toHaveAttribute("inert");
  dispose();
  expect(frames.size).toBe(0);
});

it("opening layout and focus scroll settle before launch; manual gestures still cancel", () => {
  const arrive = vi.fn(),
    settle = vi.fn();
  playFilament({ source, target, token: 3, current: () => true, arrive, settle });
  window.dispatchEvent(new Event("scroll"));
  expect(arrive).not.toHaveBeenCalled();
  expect(document.querySelector(".hp-inspector-relay")).toBeNull();
  tick(16);
  source.getBoundingClientRect = () => new DOMRect(500, 120, 200, 44);
  window.dispatchEvent(new Event("scroll"));
  tick(48);
  tick(112);
  expect(document.querySelector(".hp-inspector-relay")).toHaveAttribute("data-source-x", "696");
  expect(arrive).not.toHaveBeenCalled();
  window.dispatchEvent(new Event("wheel"));
  expect(arrive).toHaveBeenCalledTimes(1);
  expect(settle).toHaveBeenCalledTimes(1);
  expect(frames.size).toBe(0);
});
it("initial insertion is invisible, all sparks are anchored, and reveal cannot expose the full path", () => {
  const anchor = document.createElement("span");
  anchor.setAttribute("data-relay-anchor", "");
  source.append(anchor);
  anchor.getBoundingClientRect = () => new DOMRect(430, 108, 80, 28);
  const dispose = playFilament({
    source,
    target,
    token: 5,
    current: () => true,
    arrive: vi.fn(),
    settle: vi.fn(),
  });
  depart();
  const svg = document.querySelector<SVGSVGElement>(".hp-inspector-relay")!;
  expect(svg.style.opacity).toBe("0");
  expect(svg.dataset.sourceX).toBe("506");
  expect(svg.querySelector("mask path")?.getAttribute("d")).toContain("M 506 122");
  expect((svg.querySelector("mask path") as SVGElement).style.strokeDasharray).toBe("0 100000");
  svg
    .querySelectorAll(".hp-filament-shell,.hp-filament-echo,.hp-filament-core,circle")
    .forEach((node) => expect((node as SVGElement).style.opacity).toBe("0"));
  svg.querySelectorAll("circle").forEach((node) => {
    expect(node.getAttribute("cx")).toBe("506");
    expect(node.getAttribute("cy")).toBe("122");
  });
  tick(96);
  expect(Number(svg.dataset.tipLength)).toBeLessThan(10);
  expect(svg.querySelector(".hp-filament-core")?.getAttribute("stroke")).toMatch(
    /^url\(#hp-relay-/,
  );
  dispose();
});
it("return uses a short trailing segment and follows the source as the closing layout expands", () => {
  const dispose = playFilament({
    source,
    target,
    token: 6,
    current: () => true,
    closing: true,
    arrive: vi.fn(),
    settle: vi.fn(),
  });
  tick(100);
  source.getBoundingClientRect = () => new DOMRect(450, 100, 200, 44);
  tick(200);
  const svg = document.querySelector<SVGSVGElement>(".hp-inspector-relay")!;
  expect(svg.dataset.sourceX).toBe("646");
  expect(Number(svg.dataset.tailLength)).toBeGreaterThan(0);
  expect(Number(svg.dataset.tipLength) - Number(svg.dataset.tailLength)).toBeLessThan(25);
  expect(svg.querySelector("mask path")?.getAttribute("d")).toContain("646 122");
  dispose();
});
