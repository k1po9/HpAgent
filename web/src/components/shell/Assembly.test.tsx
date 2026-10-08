import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Assembly } from "./Assembly";
import { useAuth } from "../../store/auth";
import { api, type MeResponse } from "../../api/client";
beforeEach(() => {
  vi.restoreAllMocks();
  useAuth.setState({ status: "signedOut", account: null, assemblyPending: false });
});
it("marks only successful explicit authentication, and consumes it once", async () => {
  vi.spyOn(api, "me").mockResolvedValue({
    account: { account_id: "A" },
    identities: {},
    capabilities: {},
  } as MeResponse);
  await useAuth.getState().check();
  expect(useAuth.getState().assemblyPending).toBe(false);
  await useAuth.getState().check(true);
  expect(useAuth.getState().assemblyPending).toBe(true);
  const view = render(<Assembly />);
  expect(view.container.querySelector(".hp-assembly")).toHaveAttribute("aria-hidden", "true");
  expect(useAuth.getState().assemblyPending).toBe(false);
  await waitFor(() => expect(view.container.querySelector(".hp-assembly")).toBeNull());
  await useAuth.getState().check();
  expect(useAuth.getState().assemblyPending).toBe(false);
  useAuth.setState({ assemblyPending: true });
  useAuth.getState().expire();
  expect(useAuth.getState().assemblyPending).toBe(false);
});
it("removes an in-flight visual layer on runtime reduced-motion preference", () => {
  let change!: () => void;
  const media = {
    matches: false,
    addEventListener: vi.fn((_, fn) => {
      change = fn;
    }),
    removeEventListener: vi.fn(),
  };
  vi.spyOn(window, "matchMedia").mockReturnValue(media as unknown as MediaQueryList);
  useAuth.setState({ assemblyPending: true });
  const view = render(<Assembly />);
  act(() => {
    media.matches = true;
    change();
  });
  expect(view.container.querySelector(".hp-assembly")).toBeNull();
});
