import process from "node:process";

// Browser scripts write sessions and review fixtures. The operator must first
// verify the documented disposable DB/Redis/file-store/port contract.
if (!process.argv.includes("--isolated")) {
  throw new Error(
    "R1 checks require --isolated after verifying hpagent_r1_20261013 / Redis DB 14 / API 8197 / Vite 5294. Never use the daily service.",
  );
}
