export type ShellLayout = ReturnType<typeof measureShell>;
export const shellModes = { phone: 600, sidebar: 960, inspector: 1280 } as const;
export function measureShell(node: HTMLElement | null) {
  const style = getComputedStyle(node ?? document.documentElement);
  const dimension = (name: string, fallback: number) =>
    Number.parseFloat(style.getPropertyValue(name)) || fallback;
  return {
    width: node?.getBoundingClientRect().width || window.innerWidth,
    rail: dimension("--hp-rail", 104),
    sidebar: dimension("--hp-sidebar", 280),
    canvasMin: dimension("--hp-canvas-min", 600),
    inspector: dimension("--hp-inspector", 390),
    inspectorExpanded: dimension("--hp-inspector-expanded", 460),
    layoutGap: dimension("--hp-layout-gap", 4),
  };
}
export function runWidthBounds(layout: ShellLayout) {
  return {
    min: 360,
    max: Math.max(
      360,
      Math.min(880, layout.width - layout.rail - layout.canvasMin - layout.layoutGap),
    ),
  };
}
export function runInspectorWidth(layout: ShellLayout, preferred: number) {
  const bounds = runWidthBounds(layout);
  return Math.min(bounds.max, Math.max(bounds.min, preferred));
}
