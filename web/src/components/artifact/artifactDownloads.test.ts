import { afterEach, expect, it, vi } from "vitest";
import { artifactFilename, clearArtifactDownloads, downloadArtifact } from "./artifactDownloads";
afterEach(() => {
  clearArtifactDownloads();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
it("downloads frozen HTML bytes and releases object URLs on reset before the timer", async () => {
  vi.useFakeTimers();
  let blob!: Blob;
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn((value: Blob) => {
      blob = value;
      return "blob:version";
    }),
    revokeObjectURL: vi.fn(),
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  downloadArtifact("<p>历史版</p>", artifactFilename("报告/标题", 8));
  expect(blob.type).toBe("text/html");
  expect(blob.size).toBe(new TextEncoder().encode("<p>历史版</p>").length);
  expect(click.mock.instances[0]).toHaveProperty("download", "报告_标题-v8.html");
  clearArtifactDownloads();
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:version");
  await vi.advanceTimersByTimeAsync(1000);
  expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
});
