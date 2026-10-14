import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QQBindingPanel } from "./QQBindingPanel";
import { api } from "../api/client";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
it("does not create a challenge for an already bound identity", () => {
  const create = vi.spyOn(api, "createQqBindingChallenge");
  render(
    <QQBindingPanel
      qq={{ bound: true, display_subject: "QQ 已关联身份" }}
      onCompleted={vi.fn()}
      startRequested
    />,
  );
  expect(screen.getByText("QQ 已关联身份")).toBeVisible();
  expect(screen.queryByRole("button", { name: "绑定 QQ" })).toBeNull();
  expect(create).not.toHaveBeenCalled();
});
it("shows a creation error and permits a retry without claiming a binding", async () => {
  const create = vi
    .spyOn(api, "createQqBindingChallenge")
    .mockRejectedValueOnce(new Error("绑定服务暂不可用"))
    .mockResolvedValue({
      challenge_id: "controlled",
      code: "TESTONLY",
      status: "pending",
      expires_at: "2026-10-14T00:00:00Z",
    });
  render(<QQBindingPanel qq={{ bound: false }} onCompleted={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "绑定 QQ" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("绑定服务暂不可用");
  fireEvent.click(screen.getByRole("button", { name: "绑定 QQ" }));
  expect(await screen.findByText("绑定 TESTONLY")).toBeVisible();
  expect(create).toHaveBeenCalledTimes(2);
});
it("polls completion once, refreshes identity and stops the pending poll", async () => {
  vi.useFakeTimers();
  const completed = vi.fn().mockResolvedValue(undefined);
  vi.spyOn(api, "createQqBindingChallenge").mockResolvedValue({
    challenge_id: "controlled",
    code: "TESTONLY",
    status: "pending",
    expires_at: "2026-10-14T00:00:00Z",
  });
  const poll = vi.spyOn(api, "getQqBindingChallenge").mockResolvedValue({
    challenge_id: "controlled",
    status: "completed",
    expires_at: "2026-10-14T00:00:00Z",
  });
  render(<QQBindingPanel qq={{ bound: false }} onCompleted={completed} startRequested />);
  await act(async () => {});
  expect(screen.getByText("绑定 TESTONLY")).toBeVisible();
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(completed).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(9000));
  expect(poll).toHaveBeenCalledTimes(1);
});
