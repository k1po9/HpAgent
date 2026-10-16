import {
  channelPoint,
  hostPoint,
  MERCURY_SEAM,
  clamp,
  densityBins,
  particlePose,
  smooth,
  type Anchors,
  type Particle,
  type Pose,
} from "./geometry";
import { mercuryPalette, type MercuryPalette } from "./palette";
import type { MercuryStage } from "./config";
export type MercuryQuality = "balanced" | "low";
/** Bounded Canvas2D overlay. No business DOM, image capture, network or global state. */
export class MercuryRenderer {
  private readonly ctx: CanvasRenderingContext2D;
  quality: MercuryQuality = "balanced";
  private poses: Pose[] = [];
  private readonly palette: MercuryPalette;
  constructor(
    private readonly canvas: HTMLCanvasElement,
    readonly particles: readonly Particle[],
  ) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D unavailable");
    this.ctx = ctx;
    this.palette = mercuryPalette(canvas);
  }
  draw(progress: number, a: Anchors, stage: MercuryStage) {
    const dpr = Math.min(devicePixelRatio || 1, this.quality === "low" ? 1 : 1.5),
      c = this.canvas,
      ctx = this.ctx;
    const width = Math.round(innerWidth * dpr),
      height = Math.round(innerHeight * dpr);
    if (c.width !== width || c.height !== height) {
      c.width = width;
      c.height = height;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    if (progress <= 0 || progress >= 1) return;
    const stride = this.quality === "low" ? 2 : 1;
    this.poses = [];
    for (let i = 0; i < this.particles.length; i += stride)
      this.poses.push(particlePose(this.particles[i]!, progress, a));
    const r = a.reading;
    ctx.save();
    ctx.beginPath();
    ctx.rect(r.x, r.y, r.width, r.height);
    // A narrow joined corridor is the only area permitted outside reading.
    const tube = Array.from({ length: 17 }, (_, i) => {
      const t = i / 16,
        p = channelPoint(t * MERCURY_SEAM, a);
      return { ...p, w: 5 + 10 * t };
    });
    for (const [i, p] of tube.entries()) {
      if (i === 0) ctx.moveTo(p.x + p.nx * p.w, p.y + p.ny * p.w);
      else ctx.lineTo(p.x + p.nx * p.w, p.y + p.ny * p.w);
    }
    for (const p of tube.reverse()) ctx.lineTo(p.x - p.nx * p.w, p.y - p.ny * p.w);
    ctx.closePath();
    ctx.clip();
    for (const [i, p] of this.poses.entries()) {
      if (p.alpha < 0.04) continue;
      ctx.globalAlpha = p.alpha * 0.77;
      ctx.fillStyle =
        stage !== "points" && this.particles[i * stride]!.id % 20 < this.palette.heat * 20
          ? this.palette.hot
          : this.palette.neutral;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
      if (stage === "silver" && p.size > 1.4) {
        ctx.globalAlpha = p.alpha * 0.65;
        ctx.fillStyle = this.palette.core;
        ctx.fillRect(p.x - 0.35, p.y - 0.35, 0.7, 0.7);
      }
    }
    if (stage === "silver") {
      const bins = densityBins(this.poses),
        max = Math.max(1, ...bins);
      for (let i = 0; i < bins.length; i++) {
        const strength = bins[i]! / max;
        if (strength < 0.57 || bins[i]! < 1.2) continue;
        const s = (i + 0.5) / bins.length,
          lo = channelPoint(clamp(s - 0.009), a),
          hi = channelPoint(clamp(s + 0.009), a);
        ctx.globalAlpha = 0.12 * strength;
        ctx.strokeStyle = s > 0.25 && s < 0.73 ? this.palette.neutral : this.palette.reflection;
        ctx.lineWidth = 0.7 + 1.1 * strength;
        ctx.beginPath();
        ctx.moveTo(lo.x, lo.y);
        ctx.lineTo(hi.x, hi.y);
        ctx.stroke();
      }
    }
    ctx.restore();
    // Arrival confirms the real row; the incoming direction traces the same path.
    if (stage !== "points" && progress > 0.9 && progress < 1) {
      const h = hostPoint(a),
        envelope = Math.sin(Math.PI * smooth(0.9, 1, progress));
      ctx.save();
      ctx.globalAlpha = envelope * 0.34;
      ctx.strokeStyle = this.palette.hot;
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.ellipse(h.x, h.y, 3 + envelope * 3, 6 + envelope * 2, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  clear(release = false) {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (release) {
      this.canvas.width = 0;
      this.canvas.height = 0;
    }
  }
  dispose() {
    this.clear(true);
    this.poses = [];
  }
}
