import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseLocation, useClientLocation } from "./useClientLocation";

const reply = (body: unknown) => vi.fn(async () => Response.json(body));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useClientLocation", () => {
  it("resolves ip and region without sending credentials", async () => {
    const fetchMock = reply({
      success: true,
      ip: "203.0.113.9",
      country: "中国",
      region: "上海",
      city: "上海",
      country_code: "CN",
      latitude: 31.23,
      longitude: 121.47,
      connection: { isp: "Example Net" },
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useClientLocation(true));
    expect(result.current).toEqual({ status: "unavailable" });
    await waitFor(() =>
      expect(result.current).toEqual({
        status: "ready",
        ip: "203.0.113.9",
        region: "中国 上海",
        countryCode: "cn",
        position: "31.23°N 121.47°E",
        isp: "Example Net",
      }),
    );
    expect(fetchMock.mock.calls[0]).toMatchObject([
      expect.any(String),
      { credentials: "omit", referrerPolicy: "no-referrer" },
    ]);
  });

  it.each([[{ success: false }], [{ success: true, ip: 7 }], [null], ["text"]])(
    "reports a malformed answer %j as unavailable",
    async (body) => {
      vi.stubGlobal("fetch", reply(body));
      const { result } = renderHook(() => useClientLocation(true));
      await waitFor(() => expect(result.current).toEqual({ status: "unavailable" }));
    },
  );

  it("treats rate limiting as unavailable without retrying", async () => {
    const fetchMock = vi.fn(async () => new Response("slow down", { status: 429 }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useClientLocation(true));
    await waitFor(() => expect(result.current).toEqual({ status: "unavailable" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a country code unfit for a URL and impossible coordinates", () => {
    expect(
      parseLocation({
        success: true,
        ip: "203.0.113.9",
        country_code: "../x",
        latitude: 200,
        longitude: "12",
      }),
    ).toEqual({
      status: "ready",
      ip: "203.0.113.9",
      region: "",
      countryCode: null,
      position: null,
      isp: null,
    });
  });

  it("updates a slow lookup after showing unavailable by default", async () => {
    vi.useFakeTimers();
    let resolveLookup!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            resolveLookup = resolve;
            init.signal!.addEventListener("abort", () => reject(new Error("aborted")));
          }),
      ),
    );
    const { result } = renderHook(() => useClientLocation(true));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(result.current).toEqual({ status: "unavailable" });
    await act(async () => {
      resolveLookup(Response.json({ success: true, ip: "203.0.113.9", city: "上海" }));
    });
    expect(result.current).toMatchObject({ status: "ready", ip: "203.0.113.9", region: "上海" });
  });

  it("aborts on unmount and stays silent when disabled", () => {
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      signals.push(init.signal!);
      return new Promise(() => {});
    });
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useClientLocation(false));
    expect(fetchMock).not.toHaveBeenCalled();
    const { unmount } = renderHook(() => useClientLocation(true));
    unmount();
    expect(signals[0]!.aborted).toBe(true);
  });
});
