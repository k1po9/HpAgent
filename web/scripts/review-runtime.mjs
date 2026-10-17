// Internal guard for retained historical tools. No API calls or process spawning.
import process from "node:process";
import console from "node:console";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(
    "历史评审工具：必须显式 --isolated 且 HPAGENT_REVIEW_ISOLATED=1；只用于恢复后的专用评审环境。端口/场景仍取决于各工具源码。可用 HPAGENT_BROWSER_EXECUTABLE 指定浏览器；默认使用 Playwright Chromium。输出为仓库 artifacts（已忽略）。",
  );
  process.exit(0);
}
if (process.env.HPAGENT_REVIEW_ISOLATED !== "1" || !process.argv.includes("--isolated")) {
  throw new Error(
    "历史工具需要 --isolated 和 HPAGENT_REVIEW_ISOLATED=1；先核实源码中的专用端口、测试库与副作用。使用稳定 npm test:e2e 入口进行当前回归。",
  );
}
process.chdir(resolve(dirname(fileURLToPath(import.meta.url)), "../.."));
