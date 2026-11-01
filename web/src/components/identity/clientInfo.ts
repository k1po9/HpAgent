/** What this browser says about itself. Read locally, shown only here, never sent anywhere. */
export interface ClientInfo {
  language: string;
  timezone: string;
  utcOffset: string;
  screen: string;
  browser: string;
  system: string;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Takes `Date.getTimezoneOffset()`, which is positive west of UTC. */
export function formatUtcOffset(minutesWestOfUtc: number) {
  const east = -minutesWestOfUtc;
  const size = Math.abs(east);
  return `UTC${east < 0 ? "-" : "+"}${pad(Math.floor(size / 60))}:${pad(size % 60)}`;
}

// Order matters: Edge and Opera also say Chrome; Chrome also says Safari.
const BROWSERS: [string, RegExp][] = [
  ["EDGE", /\bEdg(?:e|A|iOS)?\/(\d+)/],
  ["OPERA", /\bOPR\/(\d+)/],
  ["FIREFOX", /\b(?:Firefox|FxiOS)\/(\d+)/],
  ["CHROME", /\b(?:HeadlessChrome|Chrome|CriOS)\/(\d+)/],
  ["SAFARI", /\bVersion\/(\d+)[^ ]* (?:Mobile\/\S+ )?Safari\//],
];
// Android also says Linux; iOS also says "like Mac OS X".
const SYSTEMS: [string, RegExp][] = [
  ["WINDOWS", /Windows NT/],
  ["ANDROID", /Android/],
  ["IOS", /iPhone|iPad|iPod/],
  ["MACOS", /Mac OS X|Macintosh/],
  ["CHROMEOS", /CrOS/],
  ["LINUX", /Linux|X11/],
];

/** A coarse label only; a browser may misreport, and nothing depends on this. */
export function parseUserAgent(agent: string) {
  let browser = "UNKNOWN BROWSER";
  for (const [name, pattern] of BROWSERS) {
    const match = pattern.exec(agent);
    if (match) {
      browser = `${name} ${match[1]}`;
      break;
    }
  }
  const system = SYSTEMS.find(([, pattern]) => pattern.test(agent))?.[0] ?? "UNKNOWN OS";
  return { browser, system };
}

function readTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UNKNOWN";
  } catch {
    return "UNKNOWN";
  }
}

export function readClientInfo(): ClientInfo {
  return {
    language: navigator.language || "UNKNOWN",
    timezone: readTimezone(),
    utcOffset: formatUtcOffset(new Date().getTimezoneOffset()),
    screen: `${screen.width}×${screen.height}`,
    ...parseUserAgent(navigator.userAgent),
  };
}
