import { afterEach, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkMessageItems } from "./WorkMessageItems";
import { workFixture } from "./taskFixtures";
import { useWorks } from "../../store/works";
import { useShell, parseRoute, serializeRoute } from "../../store/shell";

afterEach(() => {
  cleanup();
  useWorks.getState().reset();
  useShell.getState().reset();
});
it("uses exact source, live snapshots and clears cards on account reset", () => {
  const work = workFixture({ sources: [{ conversation_id: "c", source_message_id: "m" }] });
  useShell.setState({ route: { screen: "ai", conversationId: "c" } });
  useWorks.getState().upsert(work);
  render(<WorkMessageItems messageId="m" />);
  fireEvent.click(screen.getByRole("button"));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "task",
    objectId: work.work_id,
    origin: { conversationId: "c", messageId: "m" },
  });
  act(() => useWorks.getState().upsert({ ...work, status: "completed", row_version: 2 }));
  expect(screen.getByRole("button").textContent).toContain("已完成");
  act(() => useWorks.getState().reset());
  expect(screen.queryByRole("button")).toBeNull();
});
it("never attaches a task to another conversation or a link without a message", () => {
  useShell.setState({ route: { screen: "ai", conversationId: "other" } });
  useWorks
    .getState()
    .upsert(workFixture({ sources: [{ conversation_id: "c", source_message_id: "m" }] }));
  render(<WorkMessageItems messageId="m" />);
  expect(screen.queryByRole("button")).toBeNull();
});
it("round trips an exact message destination without an inspector", () => {
  const route = { screen: "ai" as const, conversationId: "c", messageId: "m", sourceWorkId: "w" };
  expect(parseRoute(serializeRoute(route)).route).toEqual(route);
});
