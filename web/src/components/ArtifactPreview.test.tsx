import { render, screen } from "@testing-library/react";
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
