/** Adapted from Mercury's geometry template; normalized geometry, never text pixels. */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface Anchors {
  reading: Rect;
  host: Rect;
}
export interface Particle {
  id: number;
  nx: number;
  ny: number;
  cluster: number;
  jitter: number;
  size: number;
  release: number;
}
export interface Pose {
  x: number;
  y: number;
  alpha: number;
  size: number;
  s: number;
}
export const clamp = (n: number, a = 0, b = 1) => Math.max(a, Math.min(b, n));
export const smooth = (a: number, b: number, n: number) => {
  const t = clamp((n - a) / (b - a));
  return t * t * (3 - 2 * t);
};
export function hash(n: number) {
  let x = n | 0;
  x = Math.imul(x ^ (x >>> 16), 0x7feb352d);
  x = Math.imul(x ^ (x >>> 15), 0x846ca68b);
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}
export function scopeSeed(scope: string) {
  let n = 2166136261;
  for (const c of scope) n = Math.imul(n ^ c.charCodeAt(0), 16777619);
  return n >>> 0;
}
export function phaseWave(y: number) {
  return Math.sin(y * Math.PI * 2.2) * 0.016 + Math.sin(y * 14.1 + 0.8) * 0.009;
}
export function phaseAt(x: number, y: number) {
  return 0.14 + 0.38 * (1 - x) + phaseWave(y);
}
export function phaseBoundary(progress: number, y: number) {
  return 1 - (progress - 0.14 - phaseWave(y)) / 0.38;
}
/** A single smooth edge derived from the same phase field as particle release. */
export function phasePath(progress: number) {
  const edge = Array.from({ length: 29 }, (_, i) => {
    const y = -0.2 + (i * 1.4) / 28;
    return `${clamp(phaseBoundary(progress, y), -0.3, 1.3).toFixed(4)},${y.toFixed(4)}`;
  });
  return `M-0.4,-0.2 L${edge.join(" L")} L-0.4,1.2 Z`;
}
export function makeParticles(seed: number, blocks: readonly Rect[] = [], count = 320): Particle[] {
  return Array.from({ length: count }, (_, id) => {
    const a = hash(seed + id * 17),
      b = hash(seed + id * 37 + 1),
      edge = id % 10;
    const block = blocks.length ? blocks[id % blocks.length] : undefined;
    let nx = block ? block.x + a * block.width : a,
      ny = block ? block.y + b * block.height : b;
    // 30% boundary witnesses; the rest sample visible content blocks.
    if (edge === 0) {
      nx = a;
      ny = b > 0.5 ? 0.99 : 0.01;
    }
    if (edge === 1) {
      nx = b > 0.5 ? 0.99 : 0.01;
      ny = a;
    }
    if (edge === 2) {
      nx = a;
      ny = b;
    }
    nx = clamp(nx, 0.008, 0.992);
    ny = clamp(ny, 0.008, 0.992);
    return {
      id,
      nx,
      ny,
      cluster: id % 12,
      jitter: hash(seed + id * 13 + 3) - 0.5,
      size: 0.65 + hash(seed + id * 53 + 8) * 1.15,
      release: phaseAt(nx, ny),
    };
  });
}
export function hostPoint(a: Anchors) {
  return { x: a.host.x + a.host.width - 26, y: a.host.y + a.host.height / 2 };
}
export const MERCURY_SEAM = 0.23;
export function channelPoint(s: number, a: Anchors) {
  s = clamp(s);
  const r = a.reading,
    gateY = clamp(a.host.y + a.host.height / 2, r.y + 22, r.y + r.height - 22);
  // The very same particles travel through the seam into the actual row.
  if (s < MERCURY_SEAM) {
    const h = hostPoint(a),
      t = s / MERCURY_SEAM,
      span = r.x + 8 - h.x;
    return {
      x: h.x + span * t,
      y: h.y + (gateY - h.y) * smooth(0, 1, t),
      nx:
        (-(gateY - h.y) * 6 * t * (1 - t)) /
        Math.max(1, Math.hypot(span, (gateY - h.y) * 6 * t * (1 - t))),
      ny: span / Math.max(1, Math.hypot(span, (gateY - h.y) * 6 * t * (1 - t))),
    };
  }
  s = (s - MERCURY_SEAM) / (1 - MERCURY_SEAM);
  const x = r.x + 8 + s * (r.width * 0.7 - 8);
  const y =
    gateY +
    (r.y + r.height * 0.53 - gateY) * smooth(0, 1, s) +
    Math.sin(s * Math.PI) * Math.min(54, r.height * 0.08);
  const dy =
    (r.y + r.height * 0.53 - gateY) * 6 * s * (1 - s) +
    Math.cos(s * Math.PI) * Math.PI * Math.min(54, r.height * 0.08);
  const dx = r.width * 0.7 - 8,
    len = Math.hypot(dx, dy) || 1;
  return { x, y, nx: -dy / len, ny: dx / len };
}
export function particlePose(p: Particle, progress: number, a: Anchors): Pose {
  const release = smooth(p.release - 0.035, p.release + 0.16, progress);
  const flow = smooth(p.release + 0.04, 0.96 + p.jitter * 0.018, progress),
    s = 1 - flow;
  const path = channelPoint(s, a),
    width = 18 + Math.min(45, a.reading.width * 0.035);
  const correlated = Math.sin(p.cluster * 1.618 + flow * 8.4) * 0.33;
  const offset =
    (p.jitter + correlated * 0.38) * width * (0.025 + 0.9 * s ** 0.8) +
    Math.sin(flow * 12 + p.cluster * 0.72) * 3.2 * Math.sin(Math.PI * flow);
  const startX = a.reading.x + p.nx * a.reading.width,
    startY = a.reading.y + p.ny * a.reading.height;
  return {
    x: startX + (path.x + path.nx * offset - startX) * release,
    y: startY + (path.y + path.ny * offset - startY) * release,
    alpha: smooth(p.release - 0.045, p.release + 0.06, progress) * (1 - smooth(0.98, 1, progress)),
    size: p.size,
    s,
  };
}
export function densityBins(poses: readonly Pose[], count = 44) {
  const bins = Array<number>(count).fill(0);
  for (const p of poses)
    if (p.alpha > 0.07) bins[Math.min(count - 1, Math.floor(clamp(p.s) * count))]! += p.alpha;
  return bins.map(
    (v, i) => (bins[Math.max(0, i - 1)]! + 2 * v + bins[Math.min(count - 1, i + 1)]!) / 4,
  );
}
