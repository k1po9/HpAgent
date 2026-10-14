import { afterEach, describe, expect, it } from "vitest";
import {
  armHandoff,
  leaseForSubmission,
  cancelHandoff,
  confirmHandoff,
  handoffSnapshot,
  phaseHandoff,
} from "./handoff";
import { captureBoundary, orbitGeometry, temperature } from "./geometry";
const scope = { account: "A", conversation: "C", previousRun: "old", navigation: 4 };
const source = { left: 600, top: 700, width: 38, height: 38 };
afterEach(() => cancelHandoff());
describe("single visual lease", () => {
  it("does not confirm history or the previous terminal run", () => {
    const id = armHandoff(scope, source);
    confirmHandoff(id, "old");
    expect(handoffSnapshot()?.ticket.run).toBeNull();
    confirmHandoff(id, "new");
    expect(handoffSnapshot()?.ticket.run).toBe("new");
  });
  it("stale confirmation, phase and cleanup cannot overwrite the new send", () => {
    const old = armHandoff(scope, source);
    const next = armHandoff({ ...scope, conversation: "B" }, source);
    confirmHandoff(old, "old-result");
    phaseHandoff(old!, "forming");
    cancelHandoff(old);
    expect(handoffSnapshot()?.ticket.id).toBe(next);
    expect(handoffSnapshot()?.phase).toBe("awaiting");
  });
  it("unknown acceptance remains awaiting until the same original request is confirmed", () => {
    const id = armHandoff(scope, source);
    expect(handoffSnapshot()?.phase).toBe("awaiting");
    confirmHandoff(id, "accepted");
    expect(handoffSnapshot()?.ticket.id).toBe(id);
    cancelHandoff(id);
    confirmHandoff(id, "accepted");
    expect(handoffSnapshot()).toBeNull();
  });
  it("blocked edits of an uncertain request retain its original visual identity", () => {
    const id = leaseForSubmission(scope, source, false);
    const original = handoffSnapshot()?.ticket;
    expect(leaseForSubmission(scope, { ...source, top: 800 }, true)).toBe(id);
    expect(handoffSnapshot()?.ticket).toBe(original);
    expect(leaseForSubmission({ ...scope, navigation: 99 }, source, true)).toBeUndefined();
    cancelHandoff(id);
    expect(leaseForSubmission(scope, source, true)).toBeUndefined();
  });
  it("missing source falls back without an animation lease", () => {
    expect(armHandoff(scope, null)).toBeUndefined();
    expect(handoffSnapshot()).toBeNull();
  });
});
describe("orbit contracts", () => {
  it("uses production radius, same arc endpoint, tangent and screen-space source", () => {
    const g = orbitGeometry(source, { left: 100, top: 200, width: 32, height: 32 });
    expect(g.start).toEqual({ x: 619, y: 719 });
    expect(g.center).toEqual({ x: 116, y: 216 });
    expect(g.radius).toBe(9.6);
    expect(g.path.startsWith(g.travel + " A9.6 9.6 0 1 0")).toBe(true);
  });
  it("has no formed arc before actual arrival", () => {
    expect(captureBoundary(99.999, 100, 150)).toEqual({ phase: "travelling", arc: 0 });
    expect(captureBoundary(100, 100, 150)).toEqual({ phase: "forming", arc: 0 });
    expect(captureBoundary(125, 100, 150)).toEqual({ phase: "forming", arc: 0.5 });
    expect(captureBoundary(150, 100, 150)).toEqual({ phase: "captured", arc: 1 });
  });
  it.each([
    [0, "#eb6044"],
    [0.23, "#efac91"],
    [0.48, "#dadee0"],
    [0.72, "#9ec5d1"],
    [1, "#2879bf"],
  ] as const)("samples exact temperature %s", (p, c) => expect(temperature(p)).toBe(c));
  it("keeps boundary colors continuous and rejects invalid geometry", () => {
    for (const p of [0.23, 0.48, 0.72]) {
      const a = temperature(p - 0.00001),
        b = temperature(p + 0.00001);
      expect(a).toBe(b);
    }
    expect(() => orbitGeometry(source, { left: NaN, top: 0, width: 32, height: 32 })).toThrow();
  });
});
