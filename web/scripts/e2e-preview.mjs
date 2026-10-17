// Build into a disposable directory: never overwrite a running preview's dist.
import process from "node:process";
import console from "node:console";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
const directory = await mkdtemp(join(tmpdir(), "hpagent-canonical-"));
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
      console.log(`Canonical frontend exited: code=${code} signal=${signal}`);
      resolve(code ?? 1);
    });
  });
}
try {
  const built = await run(["build", "--outDir", directory]);
  process.exitCode =
    built ||
    (stopping
      ? 0
      : await run([
          "preview",
          "--outDir",
          directory,
          "--host",
          "127.0.0.1",
          "--port",
          process.env.WEB_DEV_PORT ?? "5273",
          "--strictPort",
        ]));
  if (stopping) process.exitCode = 0;
} finally {
  await rm(directory, { recursive: true, force: true });
}
