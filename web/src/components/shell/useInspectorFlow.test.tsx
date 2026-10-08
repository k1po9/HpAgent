import { render, act } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useInspectorFlow } from "./useInspectorFlow";
it("only object changes animate the existing host, and live reduced motion cancels decoration", () => {
  const cancel = vi.fn();
  const animate = vi.fn(() => ({ cancel }));
  const dialog = document.createElement("dialog");
  const title = document.createElement("h2");
  title.id = "inspector-title";
  dialog.append(title);
  document.body.append(dialog);
  Object.defineProperty(dialog, "animate", { value: animate });
  let change!: () => void;
  const media = {
    matches: false,
    addEventListener: vi.fn((_, fn) => {
      change = fn;
    }),
    removeEventListener: vi.fn(),
  };
  const match = vi.spyOn(window, "matchMedia").mockReturnValue(media as unknown as MediaQueryList);
  function Flow({ object }: { object: string }) {
    useInspectorFlow(object);
    return null;
  }
  const view = render(<Flow object="A" />);
  view.rerender(<Flow object="A" />);
  expect(animate).toHaveBeenCalledTimes(1);
  view.rerender(<Flow object="B" />);
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(animate).toHaveBeenCalledTimes(2);
  act(() => {
    media.matches = true;
    change();
  });
  expect(cancel).toHaveBeenCalledTimes(2);
  view.rerender(<Flow object="A" />);
  expect(animate).toHaveBeenCalledTimes(2);
  view.unmount();
  dialog.remove();
  match.mockRestore();
});
