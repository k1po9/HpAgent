import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArtifactPreview } from "./ArtifactPreview";

describe("ArtifactPreview", () => {
  it("runs untrusted HTML in a script-only sandbox without same-origin", () => {
    render(<ArtifactPreview html="<html><body>artifact</body></html>" />);
    const frame = screen.getByTitle("Artifact 预览");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame).toHaveAttribute("srcdoc", "<html><body>artifact</body></html>");
  });
});

it("rejects foreign messages, clears errors on HTML change and ignores a removed frame", () => {
  const view = render(<ArtifactPreview html="<p>old</p>" />);
  const old = screen.getByTitle("Artifact 预览") as HTMLIFrameElement;
  fireEvent(
    window,
    new MessageEvent("message", {
      source: window,
      data: { type: "hpagent-artifact-runtime-error", message: "foreign" },
    }),
  );
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent(
    window,
    new MessageEvent("message", {
      source: old.contentWindow,
      data: { type: "hpagent-artifact-runtime-error", message: "old error" },
    }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("old error");
  const oldWindow = old.contentWindow;
  view.rerender(<ArtifactPreview html="<p>new</p>" />);
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent(
    window,
    new MessageEvent("message", {
      source: oldWindow,
      data: { type: "hpagent-artifact-runtime-error", message: "late error" },
    }),
  );
  expect(screen.queryByRole("alert")).toBeNull();
});
