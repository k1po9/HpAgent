import { afterEach, describe, expect, it, vi } from "vitest";
import { newIdempotencyKey } from "./idempotency";

afterEach(() => vi.unstubAllGlobals());

describe("newIdempotencyKey", () => {
  it("uses randomUUID when it is available", () => {
    const randomUUID = vi.fn(() => "550e8400-e29b-41d4-a716-446655440000");
    vi.stubGlobal("crypto", { randomUUID, getRandomValues: vi.fn() });

    expect(newIdempotencyKey()).toBe("550e8400-e29b-41d4-a716-446655440000");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("falls back to getRandomValues and returns a UUID v4", () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index));
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });

    const key = newIdempotencyKey();

    expect(getRandomValues).toHaveBeenCalledOnce();
    expect(key).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
