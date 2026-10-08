import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { RunLookup } from "./RunLookup";
import { runApi } from "../../store/runInspector";
import { useShell } from "../../store/shell";
import { HpCommandError, type HpRunSnapshot } from "../../api/types";
import { Surface } from "../shell/Surface";
function deferred() {
  let resolve!: (s: HpRunSnapshot) => void;
  const promise = new Promise<HpRunSnapshot>((r) => {
    resolve = r;
  });
  return { resolve, promise };
}
const work = (id: string) =>
  ({ source_kind: "work", run: { run_id: id, work_id: "work" } }) as HpRunSnapshot;
beforeEach(() => {
  vi.restoreAllMocks();
  useShell.getState().reset();
});
function query(value: string) {
  fireEvent.change(screen.getByLabelText("执行编号"), { target: { value } });
  fireEvent.submit(screen.getByLabelText("执行编号").closest("form")!);
}
it("verifies a Work Run and supplies Work context without a Chat message", async () => {
  const pending = deferred();
  vi.spyOn(runApi, "getRun").mockReturnValue(pending.promise);
  render(<RunLookup />);
  query("A");
  expect(useShell.getState().route.inspector).toBeUndefined();
  await act(async () => pending.resolve(work("A")));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "run",
    objectId: "A",
    origin: { workId: "work" },
  });
  expect(useShell.getState().route.inspector?.origin).not.toHaveProperty("messageId");
});
it("rejects A → B → A old success, same ID retries and results after unmount", async () => {
  const old = deferred(),
    b = deferred(),
    fresh = deferred(),
    retry = deferred();
  vi.spyOn(runApi, "getRun")
    .mockReturnValueOnce(old.promise)
    .mockReturnValueOnce(b.promise)
    .mockReturnValueOnce(fresh.promise)
    .mockReturnValueOnce(retry.promise);
  const view = render(<RunLookup />);
  query("A");
  query("B");
  query("A");
  query("A");
  await act(async () => {
    old.resolve(work("A"));
    b.resolve(work("B"));
    fresh.resolve(work("A"));
  });
  expect(useShell.getState().route.inspector).toBeUndefined();
  view.unmount();
  await act(async () => retry.resolve(work("A")));
  expect(useShell.getState().route.inspector).toBeUndefined();
});
it.each([403, 404])(
  "treats %i as unavailable and keeps the input for correction",
  async (status) => {
    vi.spyOn(runApi, "getRun").mockRejectedValue(
      new HpCommandError(status, {
        code: "not_found",
        message: "hidden",
        request_id: null,
        retryable: false,
        details: {},
      }),
    );
    render(<RunLookup />);
    query("missing");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("对象不可用。"));
    expect(screen.getByLabelText("执行编号")).toHaveValue("missing");
    expect(useShell.getState().route.inspector).toBeUndefined();
  },
);

it.each(["account", "resources"] as const)(
  "R1: a late lookup preserves the newer %s dialog",
  async (modal) => {
    const pending = deferred();
    vi.spyOn(runApi, "getRun").mockReturnValue(pending.promise);
    render(<RunLookup />);
    query("A");
    act(() => useShell.setState({ modal }));
    await act(async () => pending.resolve(work("A")));
    expect(useShell.getState().modal).toBe(modal);
    expect(useShell.getState().route.inspector).toBeUndefined();
    expect(screen.getByLabelText("执行编号")).toHaveValue("A");
  },
);

it("R1: opening and closing a Sidebar or dialog permanently invalidates the old auto-open intent", async () => {
  const pending = deferred();
  vi.spyOn(runApi, "getRun").mockReturnValue(pending.promise);
  render(<RunLookup />);
  query("A");
  act(() => {
    useShell.setState({ sidebarOpen: true });
    useShell.setState({ sidebarOpen: false });
    useShell.setState({ modal: "account" });
    useShell.setState({ modal: null });
  });
  await act(async () => pending.resolve(work("A")));
  expect(useShell.getState().route.inspector).toBeUndefined();
});

it.each([true, false])(
  "R1: a parent object's local dialog invalidates lookup even after close=%s",
  async (close) => {
    function Parent({ child }: { child: boolean }) {
      return (
        <Surface title="父对象" onClose={vi.fn()}>
          <RunLookup />
          {child && (
            <Surface title="修改要求" onClose={vi.fn()}>
              保留操作
            </Surface>
          )}
        </Surface>
      );
    }
    const pending = deferred();
    vi.spyOn(runApi, "getRun").mockReturnValue(pending.promise);
    useShell.getState().openInspector({ kind: "task", objectId: "parent" });
    const view = render(<Parent child={false} />);
    query("A");
    view.rerender(<Parent child />);
    if (close) view.rerender(<Parent child={false} />);
    await act(async () => pending.resolve(work("A")));
    expect(useShell.getState().route.inspector).toMatchObject({ kind: "task", objectId: "parent" });
  },
);

it("R1: a responsive mode change alone keeps the pending lookup's navigation intent", async () => {
  const pending = deferred();
  vi.spyOn(runApi, "getRun").mockReturnValue(pending.promise);
  const view = render(
    <Surface title="父对象" modal={false} onClose={vi.fn()}>
      <RunLookup />
    </Surface>,
  );
  query("A");
  view.rerender(
    <Surface title="父对象" modal onClose={vi.fn()}>
      <RunLookup />
    </Surface>,
  );
  await act(async () => pending.resolve(work("A")));
  expect(useShell.getState().route.inspector).toMatchObject({ kind: "run", objectId: "A" });
});
