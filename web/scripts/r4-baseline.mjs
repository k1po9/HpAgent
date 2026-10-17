import "./review-runtime.mjs";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { Buffer } from "node:buffer";
import console from "node:console";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-01");
const dom = new JSDOM("<!doctype html>", { url: "http://localhost/#/ai" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
let source = await readFile(resolve(out, "before-source/web/src/store/shell.ts"), "utf8");
source = source
  .replace(
    'from "zustand"',
    `from "${pathToFileURL(resolve("web/node_modules/zustand/esm/index.mjs"))}"`,
  )
  .replace(
    'import { useWorkbench } from "./workbench";',
    "const useWorkbench = { getState: () => ({attachments: [], activeConversationId: null}) };",
  );
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { useShell } = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);
useShell.getState().openInspector({ kind: "run", objectId: "root" });
useShell.getState().openInspector({ kind: "run", objectId: "parent" }, true);
const before = { route: useShell.getState().route, stack: useShell.getState().backStack };
useShell.setState({ dirtyTaskEditor: "draft" });
useShell.getState().openInspector({ kind: "run", objectId: "blocked-child" }, true);
const after = { route: useShell.getState().route, stack: useShell.getState().backStack };
const report = {
  source:
    "accepted R3 shell.ts, TypeScript transpilation + actual Zustand + jsdom; inert workbench read stub",
  before,
  after,
  guardPreservedRoute: before.route === after.route,
  historyChangedWhileBlocked: JSON.stringify(before.stack) !== JSON.stringify(after.stack),
};
await writeFile(
  resolve(out, "logs/baseline-guard-reproduction.json"),
  JSON.stringify(report, null, 2),
);
console.log(
  "baseline guard route preserved:",
  report.guardPreservedRoute,
  "history corrupted:",
  report.historyChangedWhileBlocked,
);
if (!report.guardPreservedRoute || !report.historyChangedWhileBlocked)
  throw new Error("baseline seam not reproduced");
