import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TraceDetail } from "./TraceDetail";
import type { ModelInputState, TraceNode } from "./traceStore";

const NODE: TraceNode = {
  id: "node-1",
  parentId: null,
  name: "LLMCall",
  type: "llm",
  status: "completed",
  startedAt: null,
  endedAt: null,
  durationMs: 10,
  metadata: {
    snapshot_id: "11111111-1111-1111-1111-111111111111",
    content_hash: "abcdef0123456789",
    fallback_attempt: 2,
  },
};

function loaded(
  visibility: "summary" | "full_safe",
  body?: Record<string, unknown>,
): ModelInputState {
  return {
    status: "loaded",
    detail: {
      visibility,
      model_input: {
        snapshot_id: String(NODE.metadata.snapshot_id),
        content_hash: String(NODE.metadata.content_hash),
        model_call_id: "call-1",
        phase: "decision",
        fallback_attempt: 2,
        endpoint_id: "endpoint:2",
        provider: "example",
        model: "fallback-model",
        api_format: "openai",
        created_at: "2026-08-22T00:00:00Z",
        message_count: 1,
        tool_count: 0,
        ...(body ? { provider_request_body: body } : {}),
      },
    },
  };
}

describe("TraceDetail model input", () => {
  it("exposes an action for a snapshot ref without fetching until clicked", async () => {
    const user = userEvent.setup();
    const open = vi.fn().mockResolvedValue(undefined);
    render(<TraceDetail node={NODE} modelInputs={{}} onOpenModelInput={open} />);

    expect(screen.getAllByText(/11111111/)).toHaveLength(2);
    expect(open).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看 Model Input" }));
    expect(open).toHaveBeenCalledWith(NODE.metadata.snapshot_id);
  });

  it("renders none and summary distinctly without hidden prompt text", () => {
    const id = String(NODE.metadata.snapshot_id);
    const { rerender } = render(
      <TraceDetail
        node={NODE}
        modelInputs={{ [id]: { status: "unavailable" } }}
        onOpenModelInput={vi.fn()}
      />,
    );
    expect(screen.getByText("当前账号不可查看 Model Input。")).toBeInTheDocument();

    rerender(
      <TraceDetail
        node={NODE}
        modelInputs={{ [id]: loaded("summary") }}
        onOpenModelInput={vi.fn()}
      />,
    );
    expect(screen.getByTestId("model-input-summary")).toHaveTextContent("Attempt 2");
    expect(screen.queryByText("HIDDEN-PROMPT-SENTINEL")).not.toBeInTheDocument();
    expect(screen.getByText("此账号仅可查看安全元数据摘要。")).toBeInTheDocument();
  });

  it("renders full_safe provider JSON as escaped data", () => {
    const id = String(NODE.metadata.snapshot_id);
    render(
      <TraceDetail
        node={NODE}
        modelInputs={{
          [id]: loaded("full_safe", { messages: [{ content: "<img src=x onerror=alert(1)>" }] }),
        }}
        onOpenModelInput={vi.fn()}
      />,
    );
    const body = screen.getByTestId("model-input-provider-body");
    expect(body).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(body.querySelector("img")).toBeNull();
  });
});
