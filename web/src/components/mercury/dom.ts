import type { Rect } from "./geometry";
export const mercuryRect = (r: DOMRect): Rect => ({
  x: r.x,
  y: r.y,
  width: r.width,
  height: r.height,
});
export function mercuryHost(region: HTMLElement, id: string) {
  return Array.from(
    region.closest(".hp-shell")?.querySelectorAll<HTMLElement>("[data-conversation-id]") ?? [],
  ).find((n) => n.dataset.conversationId === id);
}
export function mercuryHostVisible(node: HTMLElement) {
  if (!node.isConnected || node.closest("[hidden],[inert]") || !node.getClientRects().length)
    return false;
  const modal = document.querySelector("dialog:modal");
  if (modal && !modal.contains(node)) return false;
  const r = node.getBoundingClientRect();
  let left = 0,
    top = 0,
    right = innerWidth,
    bottom = innerHeight;
  for (let p: HTMLElement | null = node; p; p = p.parentElement) {
    const style = getComputedStyle(p);
    if (style.visibility !== "visible" || Number(style.opacity) === 0) return false;
    if (/auto|scroll|hidden|clip/.test(style.overflow + style.overflowY + style.overflowX)) {
      const c = p.getBoundingClientRect();
      left = Math.max(left, c.left);
      right = Math.min(right, c.right);
      top = Math.max(top, c.top);
      bottom = Math.min(bottom, c.bottom);
    }
  }
  return (
    r.width > 10 &&
    r.height > 10 &&
    Math.min(right, r.right) - Math.max(left, r.left) > r.width * 0.9 &&
    Math.min(bottom, r.bottom) - Math.max(top, r.top) > r.height * 0.9
  );
}
export function mercuryBlocks(viewport: HTMLElement, reading: Rect) {
  return Array.from(
    viewport.querySelectorAll<HTMLElement>(
      ".hp-text-part,.hp-execution-block,.hp-artifact-message-card",
    ),
  )
    .map((n) => n.getBoundingClientRect())
    .filter((r) => r.bottom > reading.y && r.top < reading.y + reading.height && r.width > 0)
    .slice(0, 32)
    .map((r) => ({
      x: Math.max(0, (r.x - reading.x) / reading.width),
      y: Math.max(0, (r.y - reading.y) / reading.height),
      width: Math.min(1, r.width / reading.width),
      height: Math.min(1, r.height / reading.height),
    }));
}
