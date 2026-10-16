import { describe, expect, it } from "vitest";
import { MercuryTimeline } from "./timeline";
import {
  densityBins,
  channelPoint,
  hostPoint,
  MERCURY_SEAM,
  makeParticles,
  particlePose,
  phaseAt,
  phaseBoundary,
  phasePath,
  scopeSeed,
} from "./geometry";
const anchors = {
  reading: { x: 384, y: 88, width: 1100, height: 650 },
  host: { x: 125, y: 220, width: 245, height: 60 },
};
describe("Mercury reversible presentation", () => {
  it("halves both host endpoint intervals while preserving the main flow and reversal state", () => {
    const measure = (start: 0 | 1, tempo: number) => {
      const t = new MercuryTimeline(start, 8.5 * 0.35, tempo);
      t.goto(start ? 0 : 1);
      let host = 0,
        main = 0;
      for (let i = 0; i < 10000 && !t.settled; i++) {
        if (t.progress >= 0.98) host += 1 / 2000;
        else main += 1 / 2000;
        t.advance(1 / 2000);
      }
      expect(t.settled).toBe(true);
      return { host, main };
    };
    for (const start of [0, 1] as const) {
      const old = measure(start, 1),
        next = measure(start, 2);
      expect(next.host).toBeCloseTo(old.host / 2, 3);
      // The phase boundary can move by one 2000 Hz sampling step.
      expect(Math.abs(next.main - old.main)).toBeLessThanOrEqual(1 / 2000 + 1e-9);
    }
    const t = new MercuryTimeline(1, 8.5 * 0.35, 2);
    t.goto(0);
    t.advance(1 / 60);
    const previous = { p: t.progress, v: t.velocity };
    t.goto(1);
    expect(t.progress).toBe(previous.p);
    expect(t.velocity).toBe(previous.v);
  });
  it("retains velocity and position on early, middle and late reversals, then reaches exact endpoints", () => {
    for (const steps of [4, 15, 35]) {
      const t = new MercuryTimeline(0);
      t.goto(1);
      for (let i = 0; i < steps; i++) t.advance(1 / 60);
      const before = { p: t.progress, v: t.velocity };
      t.goto(0);
      expect(t.progress).toBe(before.p);
      expect(t.velocity).toBe(before.v);
      for (let i = 0; i < 180; i++) t.advance(1 / 60);
      expect(t.progress).toBe(0);
      expect(t.velocity).toBe(0);
      t.goto(1);
      for (let i = 0; i < 180; i++) t.advance(1 / 60);
      expect(t.progress).toBe(1);
      expect(t.settled).toBe(true);
    }
  });
  it("handles repeated changes without overshooting or fabricating a new identity", () => {
    const t = new MercuryTimeline(0);
    for (let i = 0; i < 60; i++) {
      t.goto(i % 2 ? 0 : 1);
      t.advance(1 / 30);
      expect(t.progress).toBeGreaterThanOrEqual(0);
      expect(t.progress).toBeLessThanOrEqual(1);
    }
    t.finish(1);
    expect(t.progress).toBe(1);
    expect(t.velocity).toBe(0);
  });
  it("clamps a sampled time step rather than numerically exploding", () => {
    const a = new MercuryTimeline(0),
      b = new MercuryTimeline(0);
    a.goto(1);
    b.goto(1);
    a.advance(10);
    b.advance(1 / 30);
    expect(a.progress).toBe(b.progress);
    expect(a.velocity).toBe(b.velocity);
  });
  it("uses deterministic normalized particles for the same conversation and different seeds for another", () => {
    const seed = scopeSeed("a:c1");
    expect(makeParticles(seed)).toEqual(makeParticles(seed));
    expect(makeParticles(seed)).not.toEqual(makeParticles(scopeSeed("a:c2")));
    expect(new Set(makeParticles(seed).map((p) => p.id)).size).toBe(320);
  });
  it("derives the surface edge and particle release from the same two-dimensional phase", () => {
    for (const x of [0.1, 0.4, 0.8])
      for (const y of [0.1, 0.5, 0.9]) expect(phaseBoundary(phaseAt(x, y), y)).toBeCloseTo(x, 10);
    for (const p of makeParticles(100)) expect(p.release).toBe(phaseAt(p.nx, p.ny));
    expect(phasePath(0.3)).not.toBe(phasePath(0.4));
  });
  it("seeks either direction through identical spatial poses with no independent animation clock", () => {
    const particles = makeParticles(100);
    for (const progress of [0.14, 0.27, 0.38, 0.52, 0.7, 0.83, 0.95]) {
      const forward = particles.map((p) => particlePose(p, progress, anchors));
      const reverse = particles.map((p) => particlePose(p, progress, anchors));
      expect(forward).toEqual(reverse);
      expect(
        forward.every(
          (p) => Number.isFinite(p.x) && Number.isFinite(p.y) && p.alpha >= 0 && p.alpha <= 1,
        ),
      ).toBe(true);
    }
    expect(particles.map((p) => particlePose(p, 0, anchors)).every((p) => p.alpha === 0)).toBe(
      true,
    );
    expect(particles.map((p) => particlePose(p, 1, anchors)).every((p) => p.alpha === 0)).toBe(
      true,
    );
  });
  it("produces material only from occupied flow bins", () => {
    expect(densityBins([]).every((n) => n === 0)).toBe(true);
    const poses = makeParticles(7).map((p) => particlePose(p, 0.66, anchors));
    expect(densityBins(poses).some((n) => n > 0)).toBe(true);
  });
});

describe("real sidebar arrival", () => {
  it("carries the same particles inside the host before fading", () => {
    const h = hostPoint(anchors);
    const near = makeParticles(42).map((p) => particlePose(p, 0.96, anchors));
    expect(
      near.every(
        (p) =>
          p.alpha > 0.99 &&
          p.x < anchors.reading.x &&
          p.x > anchors.host.x &&
          p.x < anchors.host.x + anchors.host.width &&
          Math.hypot(p.x - h.x, p.y - h.y) < 3,
      ),
    ).toBe(true);
  });
  it("joins the reading trajectory to a host above or below the reading surface", () => {
    for (const y of [10, 800]) {
      const a = { ...anchors, host: { ...anchors.host, y } };
      const h = hostPoint(a),
        start = channelPoint(0, a),
        left = channelPoint(MERCURY_SEAM - 1e-8, a),
        right = channelPoint(MERCURY_SEAM + 1e-8, a);
      expect(start.x).toBe(h.x);
      expect(start.y).toBe(h.y);
      expect(Math.hypot(left.x - right.x, left.y - right.y)).toBeLessThan(0.01);
    }
  });
});
