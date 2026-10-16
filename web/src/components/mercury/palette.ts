export interface MercuryPalette {
  neutral: string;
  hot: string;
  core: string;
  reflection: string;
  heat: number;
}
/** Sample once at gesture start; review controls never alter an ongoing trajectory. */
export function mercurySpeed(canvas: HTMLCanvasElement): number {
  const raw = getComputedStyle(canvas).getPropertyValue("--hp-mercury-speed").trim();
  const speed = raw ? Number(raw) : 0.35;
  return Number.isFinite(speed) && speed > 0 ? Math.max(0.25, Math.min(1.5, speed)) : 0.35;
}
/** Resolve the live Paper Flame palette once per renderer, never during RAF. */
export function mercuryPalette(canvas: HTMLCanvasElement): MercuryPalette {
  const s = getComputedStyle(canvas),
    read = (key: string, fallback: string) => s.getPropertyValue(key).trim() || fallback;
  const heat = Number(read("--hp-mercury-heat-share", "0.15"));
  return {
    neutral: read("--hp-motion-star-neutral", "#6e8290"),
    hot: read("--hp-motion-star-hot", "#eb6044"),
    core: read("--hp-motion-star-core", "#fff3e9"),
    reflection: read("--hp-motion-reflection", "#c5d0d8"),
    heat: Number.isFinite(heat) ? Math.max(0, Math.min(1, heat)) : 0.15,
  };
}
