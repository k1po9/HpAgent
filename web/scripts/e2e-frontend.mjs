// Build into a disposable directory: never overwrite a running preview's dist.
import process from "node:process";
import console from "node:console";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
const mode = process.env.HPAGENT_E2E_FRONTEND_MODE ?? "production";
if (!["dev", "preview", "production"].includes(mode)) throw new Error("Invalid frontend mode");
const port = Number(process.env.WEB_DEV_PORT ?? 5273);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid frontend port");
if (process.env.HPAGENT_E2E_API_READY_URL) {
  const response = await globalThis.fetch(process.env.HPAGENT_E2E_API_READY_URL, {
    signal: globalThis.AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error("The explicitly supplied isolated API is not ready");
}
const directory =
  mode === "production"
    ? await mkdtemp(join(tmpdir(), "hpagent-browser-"))
    : (process.env.HPAGENT_E2E_BUILD_DIR ?? "dist");
let child;
let stopping = false;
const stop = () => {
  stopping = true;
  child?.kill("SIGTERM");
};
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
async function run(args) {
  child = spawn(process.execPath, ["node_modules/vite/bin/vite.js", ...args], { stdio: "inherit" });
  return new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code, signal) => {
      console.log(`Browser frontend exited: code=${code} signal=${signal}`);
      resolve(code ?? 1);
    });
  });
}
try {
  const built = mode === "production" ? await run(["build", "--outDir", directory]) : 0;
  process.exitCode =
    built ||
    (stopping
      ? 0
      : await run([
          ...(mode === "dev" ? [] : ["preview", "--outDir", directory]),
          "--host",
          "127.0.0.1",
          "--port",
          String(port),
          "--strictPort",
        ]));
  if (stopping) process.exitCode = 0;
} finally {
  if (mode === "production") await rm(directory, { recursive: true, force: true });
}
