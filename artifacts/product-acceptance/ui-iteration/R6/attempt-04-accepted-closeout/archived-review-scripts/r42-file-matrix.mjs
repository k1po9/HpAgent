/* global window, document, getComputedStyle, URLSearchParams, console, process */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const conversation = "01a13187-fc64-7b62-bc70-bc5a6d25c0ad";
const matrix = [
  ["run:r4-A", "overview", ".hp-vitality-facts"],
  ["run:r4-A", "resources", '[aria-label="使用资料与输出"]'],
  ["run:r4-A", "advanced", ".hp-trace-tree__node"],
  ["artifact:r42-B", "preview", 'iframe[title="Artifact 预览"]'],
  ["artifact:r42-B", "versions", ".hp-artifact-history"],
  ["artifact:r42-B", "details", ".hp-artifact-details"],
  ["file:ai-output-node", "preview", ".hp-file-preview"],
  ["file:ai-output-node", "details", '[aria-label="保留与空间"]'],
  ["file:ai-output-node", "versions", '[aria-label="Workspace 版本"]'],
  ["file:ai-output-node", "usage", ".hp-subject-picker"],
  ["file:ai-folder", "details", ".hp-object-path"],
  ["file:ai-folder", "usage", ".hp-subject-picker"],
  ["task:ai-task", "overview", '[aria-label="任务概览"]'],
  ["task:ai-task", "outputs", ".hp-task-outputs"],
  ["task:ai-task", "resources", ".hp-operation-form"],
  ["task:ai-task", "advanced", 'input[name="run"]'],
];
const reports = [],
  errors = [],
  network = [],
  failures = [];
const styles = [
  "display",
  "fontSize",
  "fontWeight",
  "lineHeight",
  "color",
  "backgroundColor",
  "borderRadius",
  "padding",
  "gap",
];
const targets = [
  ".hp-inspector",
  ".hp-surface-header",
  ".hp-surface-body",
  ".hp-inspector-tabs",
  ".hp-inspector-tabs button",
  ".hp-inspector-card",
  ".hp-artifact-heading",
  ".hp-artifact-content",
  ".hp-artifact-composer",
  ".hp-artifact-toolbar",
  ".hp-artifact-details",
  ".hp-artifact-history li",
  ".hp-file-preview",
  ".hp-file-inspector dl",
  ".hp-task-inspector dl",
  ".hp-task-report",
  ".hp-trace-detail",
  ".hp-trace-tree-wrap",
];
async function pageFor(stage, kind, tab) {
  const p = await browser.newPage({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "reduce",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  });
  p.on("pageerror", (e) => errors.push({ stage, kind, tab, error: e.message }));
  p.on("request", (r) => {
    if (/^https?:/.test(r.url())) network.push({ stage, url: r.url() });
  });
  const file =
    stage === "target"
      ? out + "/inputs/HpAgent_Inspector_R2_Visual_System_Integrated_v1.1.html"
      : out + `/offline/${stage.toUpperCase()}.React.Standalone.html`;
  const params = new URLSearchParams({ inspect: kind, tab });
  if (kind.startsWith("artifact:")) params.set("version", "r42-B-v2");
  await p.goto(pathToFileURL(file).href + `#/ai/${conversation}?${params}`);
  await p.waitForFunction(() => window.__handoffBooted === true);
  await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
  return p;
}
async function snapshot(p) {
  return p.evaluate(
    ({ targets, styles }) => {
      const root = document.querySelector(".hp-inspector");
      const origin = root.getBoundingClientRect();
      const nodes = targets.flatMap((selector) =>
        [...root.querySelectorAll(selector === ".hp-inspector" ? ":scope" : selector)]
          .filter((n) => n.getBoundingClientRect().height > 0 && !n.closest("[hidden]"))
          .slice(0, 8)
          .map((n, index) => {
            const r = n.getBoundingClientRect(),
              s = getComputedStyle(n);
            return {
              selector,
              index,
              rect: [r.x - origin.x, r.y - origin.y, r.width, r.height].map(
                (x) => Math.round(x * 10) / 10,
              ),
              css: Object.fromEntries(styles.map((k) => [k, s[k]])),
            };
          }),
      );
      return {
        nodes,
        tabs: [...root.querySelectorAll('[role="tab"]')].map((n) => ({
          text: n.textContent,
          selected: n.getAttribute("aria-selected"),
          tabIndex: n.tabIndex,
        })),
        focus: document.activeElement?.id || document.activeElement?.tagName,
        requests: window.__handoffLog,
        hiddenLeaks: [...root.querySelectorAll("[hidden]")]
          .filter((n) => getComputedStyle(n).display !== "none")
          .map((n) => n.outerHTML.slice(0, 180)),
      };
    },
    { targets, styles },
  );
}
try {
  for (const [kind, tab, selector] of matrix) {
    for (const stage of ["before", "after"]) {
      let p;
      try {
        p = await pageFor(stage, kind, tab);
        await expect(p.locator(selector).first()).toBeVisible();
        // Wait for data, then use OS reduced motion for deterministic captures.
        if (tab === "resources" && kind.startsWith("run:"))
          await expect(
            p.getByRole("button", { name: "项目资料/项目评审说明.md", exact: true }),
          ).toBeVisible();
        if (tab === "outputs") await expect(p.locator(".hp-task-artifact").first()).toBeVisible();
        if (tab === "usage") await p.getByLabel(/^目标对话/).selectOption(conversation);
        await p.waitForTimeout(150);
        const base = kind.replace(":", "-") + "-" + tab;
        for (const width of [390, 460]) {
          if (width === 460) await p.locator(".hp-inspector-expand").click();
          expect(
            Math.round(
              await p.locator(".hp-inspector").evaluate((n) => n.getBoundingClientRect().width),
            ),
          ).toBe(width);
          await p
            .locator(".hp-surface-body")
            .last()
            .evaluate((n) => (n.scrollTop = 0));
          await p.screenshot({ path: `${out}/screenshots/${stage}/${base}-${width}.png` });
          const state = await snapshot(p);
          expect(state.hiddenLeaks).toEqual([]);
          reports.push({ stage, kind, tab, width, result: "pass", ...state });
        }
        // Include the full scroll surface, deep native disclosures and bottom controls.
        const expanded = await p.evaluate(() => {
          const root = document.querySelector(".hp-inspector");
          const details = [...root.querySelectorAll("details")].filter(
            (n) => !n.closest("[hidden]"),
          );
          details.forEach((n) => (n.open = true));
          return details.map((n) => n.querySelector("summary")?.textContent);
        });
        await p
          .locator(".hp-surface-body")
          .last()
          .evaluate((n) => (n.scrollTop = n.scrollHeight));
        await p.screenshot({ path: `${out}/screenshots/${stage}/${base}-deep-bottom.png` });
        reports.push({ stage, kind, tab, deepDisclosure: expanded, result: "pass" });
      } catch (e) {
        failures.push({ stage, kind, tab, error: String(e) });
        if (p)
          await p.screenshot({
            path: `${out}/logs/failure-${stage}-${kind.replace(":", "-")}-${tab}.png`,
          });
      } finally {
        await p?.close();
      }
    }
    console.log("captured", kind, tab);
  }
  const parity = [];
  for (const target of reports.filter((r) => r.stage === "before" && r.nodes)) {
    const after = reports.find(
      (r) =>
        r.stage === "after" &&
        r.kind === target.kind &&
        r.tab === target.tab &&
        r.width === target.width,
    );
    const differences = [];
    if (!after) {
      differences.push("missing after");
    } else {
      if (JSON.stringify(target.tabs) !== JSON.stringify(after.tabs))
        differences.push({ tabs: [target.tabs, after.tabs] });
      for (const node of target.nodes) {
        const other = after.nodes.find(
          (n) => n.selector === node.selector && n.index === node.index,
        );
        if (!other || JSON.stringify(node) !== JSON.stringify(other))
          differences.push({ target: node, after: other });
      }
    }
    parity.push({
      kind: target.kind,
      tab: target.tab,
      width: target.width,
      result: differences.length ? "difference" : "pass",
      differences,
    });
  }
  await writeFile(
    out + "/evidence/visual-matrix.json",
    JSON.stringify(
      {
        browser: browser.version(),
        viewport: [1680, 1000],
        motion: "reduce",
        reports,
        parity,
        failures,
        errors,
        network,
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({
      captures: reports.length,
      parity: parity.map((x) => ({
        kind: x.kind,
        tab: x.tab,
        width: x.width,
        differences: x.differences.length,
      })),
      failures,
      errors,
      network,
    }),
  );
  if (failures.length || errors.length || network.length) process.exitCode = 1;
} finally {
  await browser.close();
}
