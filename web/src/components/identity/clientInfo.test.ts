import { describe, expect, it } from "vitest";
import { formatUtcOffset, parseUserAgent } from "./clientInfo";

describe("clientInfo", () => {
  it.each([
    [-480, "UTC+08:00"],
    [0, "UTC+00:00"],
    [300, "UTC-05:00"],
    [-345, "UTC+05:45"],
  ])("formats an offset of %i minutes as %s", (minutes, text) => {
    expect(formatUtcOffset(minutes)).toBe(text);
  });

  it.each([
    [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
      "EDGE 130",
      "WINDOWS",
    ],
    [
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.1 Safari/605.1.15",
      "SAFARI 18",
      "MACOS",
    ],
    [
      "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
      "FIREFOX 132",
      "LINUX",
    ],
    [
      "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile",
      "CHROME 130",
      "ANDROID",
    ],
    [
      "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/147.0.0.0 Safari/537.36",
      "CHROME 147",
      "LINUX",
    ],
    ["something else", "UNKNOWN BROWSER", "UNKNOWN OS"],
  ])("reads %s", (agent, browser, system) => {
    expect(parseUserAgent(agent)).toEqual({ browser, system });
  });
});
