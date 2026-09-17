import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api } from "../api/client";
import { ApprovalCard } from "./ApprovalCard";

const pending = {
  approval_id: "a1",
  run_id: "r1",
  operation_id: "op",
  tool_name: "save_persistent_file",
  action_summary: "Overwrite",
  status: "pending" as const,
  logical_path: "research/agent-memory.md",
  expected_revision: 1,
};

it("restores a pending card and prevents duplicate approve clicks", async () => {
  const request = vi.spyOn(api, "request").mockImplementation(async (options) => {
    if (options.method === "GET") return { approvals: [pending] } as never;
    return { approval: { ...pending, status: "approved" } } as never;
  });
  render(<ApprovalCard runId="r1" />);
  expect(await screen.findByText(/research\/agent-memory\.md/)).toBeInTheDocument();
  expect(screen.getByText(/当前版本：revision 1/)).toBeInTheDocument();
  const button = screen.getByRole("button", { name: "确认更新" });
  await userEvent.click(button);
  await userEvent.click(button);
  await waitFor(() => expect(screen.getByText("已批准，正在继续执行…")).toBeInTheDocument());
  expect(request.mock.calls.filter(([value]) => value.method === "POST")).toHaveLength(1);
  request.mockRestore();
});

it.each(["rejected", "expired", "cancelled"] as const)(
  "renders %s without actions",
  async (status) => {
    vi.spyOn(api, "request").mockResolvedValueOnce({
      approvals: [{ ...pending, status }],
    } as never);
    render(<ApprovalCard runId="r1" />);
    await screen.findByTestId("approval-card");
    expect(screen.queryByRole("button", { name: "确认更新" })).not.toBeInTheDocument();
    vi.restoreAllMocks();
  },
);

it("rejects once and removes both actions", async () => {
  const request = vi.spyOn(api, "request").mockImplementation(async (options) => {
    if (options.method === "GET") return { approvals: [pending] } as never;
    return { approval: { ...pending, status: "rejected" } } as never;
  });
  render(<ApprovalCard runId="r1" />);
  await userEvent.click(await screen.findByRole("button", { name: "拒绝" }));
  await waitFor(() => expect(screen.getByText("已拒绝")).toBeInTheDocument());
  expect(request.mock.calls.filter(([value]) => value.method === "POST")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "确认更新" })).not.toBeInTheDocument();
  request.mockRestore();
});

it("shows the authoritative destination revision and authenticated download", async () => {
  vi.spyOn(api, "request")
    .mockResolvedValueOnce({ approvals: [{ ...pending, status: "consumed" }] } as never)
    .mockResolvedValueOnce({
      destination: {
        logical_path: pending.logical_path,
        current_revision: 2,
        current_file_id: "file-2",
        current_sha256: "sha-2",
        last_operation_id: pending.operation_id,
      },
    } as never);
  render(<ApprovalCard runId="r1" />);
  expect(await screen.findByText("最新版本：revision 2")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载文件" })).toHaveAttribute(
    "href",
    "/api/v1/files/file-2/content",
  );
  vi.restoreAllMocks();
});
