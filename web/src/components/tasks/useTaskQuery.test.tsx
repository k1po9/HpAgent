import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { HpCommandError } from "../../api/types";
import { useWorks } from "../../store/works";
import { resetTaskQueries, useTaskQuery } from "./useTaskQuery";
function Read({ id, label = id }: { id: string; label?: string }) {
  const query = useTaskQuery<{ body: string }>(id, `/reports/${id}`);
  return (
    <section aria-label={label}>
      <p>{query.data?.body ?? "loading"}</p>
      {query.error && <p role="alert">{query.error}</p>}
      <button onClick={query.retry}>retry {label}</button>
    </section>
  );
}
afterEach(() => {
  cleanup();
  useWorks.getState().reset();
  resetTaskQueries();
  vi.restoreAllMocks();
});
it("deduplicates StrictMode readers and limits report/file requests to three", async () => {
  const pending: Array<(value: unknown) => void> = [];
  const spy = vi.spyOn(api, "request").mockImplementation(
    () =>
      new Promise((resolve) => {
        pending.push(resolve);
      }) as never,
  );
  render(
    <StrictMode>
      <Read id="a" />
      <Read id="a" label="second-a" />
      <Read id="b" />
      <Read id="c" />
      <Read id="d" />
    </StrictMode>,
  );
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(3));
  await act(async () => pending[0]!({ body: "report-a" }));
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(4));
  expect(screen.getAllByText("report-a")).toHaveLength(2);
  await act(async () => pending.slice(1).forEach((finish, i) => finish({ body: `body-${i}` })));
  await waitFor(() => expect(screen.queryByText("loading")).toBeNull());
});
it("never shows another report or an old-account body after switching or resetting", async () => {
  const pending: Array<(value: unknown) => void> = [];
  vi.spyOn(api, "request").mockImplementation(
    () => new Promise((resolve) => pending.push(resolve)) as never,
  );
  const view = render(<Read id="a" />);
  view.rerender(<Read id="b" />);
  await act(async () => pending[0]!({ body: "private-a" }));
  expect(screen.queryByText("private-a")).toBeNull();
  await act(async () => pending[1]!({ body: "current-b" }));
  expect(screen.getByText("current-b")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "retry b" }));
  await act(async () => {
    useWorks.getState().reset();
    resetTaskQueries();
  });
  await waitFor(() => expect(pending).toHaveLength(4));
  await act(async () => pending[3]!({ body: "new-account-b" }));
  await act(async () => pending[2]!({ body: "old-account-secret" }));
  expect(screen.queryByText("old-account-secret")).toBeNull();
  expect(screen.getByText("new-account-b")).toBeVisible();
});
it("retains the same report on network failure but clears it on permission denial", async () => {
  vi.spyOn(api, "request")
    .mockResolvedValueOnce({ body: "verified-report" })
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockRejectedValueOnce(
      new HpCommandError(403, {
        code: "forbidden",
        message: "no access",
        request_id: "test",
        retryable: false,
        details: {},
      }),
    );
  render(<Read id="a" />);
  await screen.findByText("verified-report");
  fireEvent.click(screen.getByRole("button", { name: "retry a" }));
  await screen.findByRole("alert");
  expect(screen.getByText("verified-report")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "retry a" }));
  await screen.findByText("对象不可用。");
  expect(screen.queryByText("verified-report")).toBeNull();
});
