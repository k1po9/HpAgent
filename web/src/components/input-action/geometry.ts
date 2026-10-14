export type Rect = { left: number; top: number; width: number; height: number };
export function orbitGeometry(source: Rect, target: Rect) {
  for (const r of [source, target])
    if (![r.left, r.top, r.width, r.height].every(Number.isFinite) || r.width <= 0 || r.height <= 0)
      throw new Error("Invalid relay geometry");
  const start = { x: source.left + source.width / 2, y: source.top + source.height / 2 };
  const center = { x: target.left + target.width / 2, y: target.top + target.height / 2 };
  // C's real glyph is viewBox 40x40, radius 12; do not use the lab's 14/36 ratio.
  const radius = (Math.min(target.width, target.height) * 12) / 40;
  const entry = { x: center.x - radius, y: center.y };
  const end = {
    x: center.x + radius * Math.cos((-140 * Math.PI) / 180),
    y: center.y + radius * Math.sin((-140 * Math.PI) / 180),
  };
  const lane = Math.max(8, target.top - 40);
  const travel = `M${start.x} ${start.y} C${Math.max(8, start.x - Math.min(340, Math.abs(start.x - entry.x) * 0.36))} ${lane},${entry.x} ${lane},${entry.x} ${entry.y}`;
  return {
    start,
    center,
    radius,
    travel,
    path: `${travel} A${radius} ${radius} 0 1 0 ${end.x} ${end.y}`,
  };
}
const colors = [
  [235, 96, 68],
  [239, 172, 145],
  [218, 222, 224],
  [158, 197, 209],
  [40, 121, 191],
];
const stops = [0, 0.23, 0.48, 0.72, 1];
export function temperature(value: number) {
  const p = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  let i = 0;
  while (i < 3 && p > stops[i + 1]!) i++;
  const t = (p - stops[i]!) / (stops[i + 1]! - stops[i]!);
  return (
    "#" +
    colors[i]!.map((n, c) =>
      Math.round(n + (colors[i + 1]![c]! - n) * t)
        .toString(16)
        .padStart(2, "0"),
    ).join("")
  );
}
export function captureBoundary(head: number, travel: number, total: number) {
  if (!(travel > 0 && total > travel)) throw new Error("Invalid relay lengths");
  return {
    phase: head < travel ? "travelling" : head < total ? "forming" : "captured",
    arc: Math.max(0, Math.min(1, (head - travel) / (total - travel))),
  };
}
