import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type MeResponse } from "../api/client";
import { useAuth } from "./auth";

const me = {
  account: { account_id: "entry-account", status: "active", created_at: "2026-10-10T00:00:00Z" },
  session: {},
  csrf_token: "fixture",
  identities: { web: { username: "entry" }, qq: { bound: true } },
  capabilities: {},
} as MeResponse;

beforeEach(() => {
  useAuth.getState().expire();
  useAuth.setState({ status: "checking", entrySource: "restore" });
});
afterEach(() => {
  useAuth.getState().expire();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("entry lifecycle", () => {
  it("arms restoration once, without replaying on background checks", async () => {
    vi.spyOn(api, "me").mockResolvedValue(me);
    await useAuth.getState().check();
    expect(useAuth.getState()).toMatchObject({
      status: "signedIn",
      entrySource: "restore",
      assemblyPending: true,
    });
    useAuth.setState({ assemblyPending: false });
    await useAuth.getState().check();
    expect(useAuth.getState().assemblyPending).toBe(false);
  });
  it("arms the login origin after explicit authentication", async () => {
    vi.spyOn(api, "me").mockResolvedValue(me);
    await useAuth.getState().check(true);
    expect(useAuth.getState()).toMatchObject({ entrySource: "login", assemblyPending: true });
  });
  it("expired cookies never arm a success animation", async () => {
    vi.spyOn(api, "me").mockResolvedValue(null);
    await useAuth.getState().check();
    expect(useAuth.getState()).toMatchObject({ status: "signedOut", assemblyPending: false });
  });
  it("aborts a stalled probe after 12 seconds and exposes retry state", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    vi.spyOn(api, "me").mockImplementation((s) => {
      signal = s;
      return new Promise((_, reject) =>
        s?.addEventListener("abort", () => reject(new DOMException("Timed out", "AbortError"))),
      );
    });
    const request = useAuth.getState().check();
    await vi.advanceTimersByTimeAsync(12000);
    await request;
    expect(signal?.aborted).toBe(true);
    expect(useAuth.getState()).toMatchObject({ status: "error", assemblyPending: false });
  });
  it("a late successful probe cannot restore an expired account", async () => {
    let resolve!: (value: MeResponse) => void;
    vi.spyOn(api, "me").mockImplementation(() => new Promise((r) => (resolve = r)));
    const request = useAuth.getState().check();
    useAuth.getState().expire();
    resolve(me);
    await request;
    expect(useAuth.getState()).toMatchObject({
      status: "signedOut",
      account: null,
      assemblyPending: false,
    });
  });
});
