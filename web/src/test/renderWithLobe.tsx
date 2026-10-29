import { render as renderReact, type RenderOptions } from "@testing-library/react";
import type { ReactNode } from "react";
import { LobeBoundary } from "../components/ui/LobeBoundary";

/** Exercise the same locale and motion provider that main.tsx supplies. */
export function render(ui: ReactNode, options?: Omit<RenderOptions, "wrapper">) {
  return renderReact(ui, { ...options, wrapper: LobeBoundary });
}
