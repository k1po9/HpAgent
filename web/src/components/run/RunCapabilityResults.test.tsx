import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { api } from "../../api/client";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { RunCapabilityResults } from "./RunCapabilityResults";

afterEach(() => {
  cleanup();
  useWorks.getState().reset();
  useShell.getState().reset();
  vi.restoreAllMocks();
});
it("reads the selected research instance and opens the exact HTML version", async () => {
  const request = vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.includes("research/report"))
      return { report: { report_markdown: "# Second report" } } as never;
    return {
      items: [
        {
          invocation_id: "second:research",
          capability_key: "research",
          payload: {
            outcome: "insufficient",
            summary: "Missing evidence",
            outputs: [],
            checks: [],
            gaps: ["missing citation"],
          },
        },
        {
          invocation_id: "html",
          capability_key: "html",
          payload: {
            outcome: "succeeded",
            summary: "Page",
            checks: [],
            gaps: [],
            outputs: [{ ref_id: "ref", type: "artifact_version", object_id: "a", version: "v2" }],
          },
        },
      ],
      truncated: false,
    } as never;
  });
  render(<RunCapabilityResults runId="r" />);
  fireEvent.click(screen.getByText("读取能力成果"));
  await screen.findByText("Missing evidence");
  fireEvent.click(screen.getByText("读取此研究报告"));
  await screen.findByRole("heading", { name: "Second report" });
  expect(
    request.mock.calls.some(([r]) => r.path.endsWith("?invocation_id=second%3Aresearch")),
  ).toBe(true);
  fireEvent.click(screen.getByText("查看 HTML 成果版本"));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "artifact",
    objectId: "a",
    versionId: "v2",
  });
});
