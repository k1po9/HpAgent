export interface ParticleRect {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface Particle {
  x: number;
  y: number;
  tx: number;
  ty: number;
  bend: number;
  size: number;
  accent: boolean;
  delay: number;
  travel: number;
}
// Deterministic edge-to-edge flow. Every fourth particle belongs to the same
// destination, so reducing the budget preserves all four assembly stages.
export function makeParticles(
  source: ParticleRect,
  targets: ParticleRect[],
  count: number,
  shape: "rect" | "ring" = "rect",
) {
  const visibleTargets = targets.filter((r) => r.width > 0 && r.height > 0);
  if (!visibleTargets.length) return [];
  return Array.from({ length: count }, (_, i) => {
    const group = i % visibleTargets.length;
    const target = visibleTargets[group]!;
    const t = (i * 0.61803398875) % 1;
    const edge = Math.floor(i / visibleTargets.length) % 4;
    const inset = 16; // Avoid rounded corners of the Auth surface.
    const x =
      source.x +
      (edge === 0 ? 1 : edge === 1 ? source.width - 1 : inset + t * (source.width - inset * 2));
    const y =
      source.y +
      (edge === 2 ? 1 : edge === 3 ? source.height - 1 : inset + t * (source.height - inset * 2));
    // Follow vertical shell boundaries and the upper edge of the Composer.
    const horizontal = group === visibleTargets.length - 1;
    return {
      x:
        shape === "ring"
          ? source.x + source.width / 2 + Math.cos(t * Math.PI * 2) * (source.width / 2 - 6)
          : x,
      y:
        shape === "ring"
          ? source.y + source.height / 2 + Math.sin(t * Math.PI * 2) * (source.height / 2 - 6)
          : y,
      tx: horizontal ? target.x + 12 + t * (target.width - 24) : target.x + target.width - 1,
      ty: horizontal ? target.y + 1 : target.y + 18 + t * (target.height - 36),
      bend: (i % 2 ? 1 : -1) * (12 + (i % 24)),
      size: 1 + (i % 3),
      accent: i % 23 === 0,
      delay: group * 0.075 + (i % 5) * 0.009,
      travel: 0.45 + group * 0.06,
    };
  });
}

export function createParticlePainter(
  canvas: HTMLCanvasElement | null,
  particles: Particle[],
  width: number,
  height: number,
  resolution: number,
) {
  const ctx = canvas?.getContext("2d");
  if (!ctx || !canvas || typeof Path2D === "undefined") return null;
  canvas.width = Math.round(width * resolution);
  canvas.height = Math.round(height * resolution);
  ctx.scale(resolution, resolution);
  // Carry the live product palette from the ring/card into the particles.
  const theme = getComputedStyle(canvas);
  const accent = theme.getPropertyValue("--hp-accent").trim() || "#1267df";
  const neutral = theme.getPropertyValue("--hp-muted").trim() || "#67758c";
  // Precompute positions/alpha once. The live frame only fills cached paths;
  // it does no trigonometry and performs at most eight fill calls.
  const steps = 80;
  const frames = Array.from({ length: steps + 1 }, (_, frame) => {
    const progress = frame / steps;
    const paths = Array.from({ length: 8 }, () => new Path2D());
    let left = width,
      top = height,
      right = 0,
      bottom = 0;
    for (const p of particles) {
      const t = Math.max(0, Math.min(1, (progress - p.delay) / p.travel));
      if (t <= 0 || t >= 1) continue;
      const ease = t * t * (3 - 2 * t);
      const alpha = Math.min(1, t / 0.16, (1 - t) / 0.22);
      const bucket = Math.min(3, Math.floor(alpha * 4)) + (p.accent ? 4 : 0);
      const x = p.x + (p.tx - p.x) * ease;
      const y = p.y + (p.ty - p.y) * ease + Math.sin(t * Math.PI) * p.bend;
      paths[bucket]!.rect(x, y, p.size, p.size);
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x + p.size);
      bottom = Math.max(bottom, y + p.size);
    }
    return {
      paths,
      bounds: right
        ? ([left - 2, top - 2, right - left + 4, bottom - top + 4] as [
            number,
            number,
            number,
            number,
          ])
        : null,
    };
  });
  let previous: [number, number, number, number] | null = null;
  let lastFrame = -1,
    disposed = false;
  const clear = () => {
    if (previous) ctx.clearRect(...previous);
    previous = null;
    lastFrame = -1;
  };
  return {
    draw(progress: number) {
      if (disposed) return;
      const index = Math.min(steps, Math.max(0, Math.round(progress * steps)));
      if (index === lastFrame) return;
      if (previous) ctx.clearRect(...previous);
      const frame = frames[index]!;
      for (let group = 0; group < 2; group++) {
        ctx.fillStyle = group ? accent : neutral;
        for (let alpha = 0; alpha < 4; alpha++) {
          ctx.globalAlpha = (alpha + 1) * 0.21;
          ctx.fill(frame.paths[group * 4 + alpha]!);
        }
      }
      previous = frame.bounds;
      lastFrame = index;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      clear();
      frames.length = 0;
      canvas.width = 0;
      canvas.height = 0;
    },
  };
}
