# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: ui-7-unification.spec.ts >> all four Inspectors have keyboard tabs, URL replace, geometry and screenshots
- Location: e2e/ui-7-unification.spec.ts:189:1

# Error details

```
Error: expect(received).toBeGreaterThanOrEqual(expected)

Expected: >= 0
Received:    -191.90625
```

# Page snapshot

```yaml
- generic [ref=e4]:
  - generic [ref=e5]:
    - generic "HpAgent" [ref=e6]: H
    - navigation "主导航" [ref=e7]:
      - button "AI" [ref=e8] [cursor=pointer]
      - button "空间" [ref=e13] [cursor=pointer]
      - button "任务" [ref=e17] [cursor=pointer]
    - button "账户设置" [ref=e22] [cursor=pointer]
  - main [ref=e26]:
    - generic [ref=e27]:
      - button "打开侧栏" [ref=e28] [cursor=pointer]
      - heading "AI" [level=1] [ref=e30]
      - group [ref=e31]:
        - generic "按执行编号查询" [ref=e32] [cursor=pointer]
    - region "对话页面" [ref=e33]:
      - generic [ref=e36]:
        - generic [ref=e37]: 开始新的对话吧
        - generic [ref=e40]:
          - generic "长期授权资料" [ref=e42]:
            - button "使用资料" [ref=e43] [cursor=pointer]
          - textbox "消息输入" [ref=e44]:
            - /placeholder: 输入消息，Enter 发送
          - generic [ref=e45]:
            - generic "添加附件" [ref=e46] [cursor=pointer]:
              - generic [ref=e49]: 附件
              - button "添加附件" [ref=e50]
            - group [ref=e51]:
              - generic "选择已有文件" [ref=e52] [cursor=pointer]
            - combobox "执行模式" [ref=e53]:
              - option "快速" [selected]
              - option "深度"
            - button "发送" [disabled] [ref=e54]
  - complementary "HTML 成果" [ref=e56]:
    - generic [ref=e57]:
      - heading "HTML 成果" [level=2] [ref=e58]
      - button "关闭HTML 成果" [ref=e59] [cursor=pointer]: ×
    - generic [ref=e60]:
      - button "扩大阅读" [ref=e61] [cursor=pointer]
      - generic "HTML Artifact" [ref=e62]:
        - generic [ref=e63]:
          - heading "很长的中文标题和英文路径 / LongTitleWithoutSpaces很长的中文标题和英文路径 / LongTitleWithoutSpaces很长的中文标题和英文路径 / LongTitleWithoutSpaces很长的中文标题和英文路径 / LongTitleWithoutSpaces" [level=3] [ref=e64]
          - paragraph [ref=e65]: HTML · 正在查看 v1 · 生成完成
        - status [ref=e66]:
          - text: v1 · 生成完成
          - button "查看 v1" [ref=e67] [cursor=pointer]
        - tablist "HTML 成果内容" [ref=e68]:
          - tab "预览" [active] [selected] [ref=e69] [cursor=pointer]
          - tab "版本历史" [ref=e70] [cursor=pointer]
          - tab "详情" [ref=e71] [cursor=pointer]
        - tabpanel "预览" [ref=e72]:
          - generic [ref=e73]:
            - button "下载 HTML" [ref=e74] [cursor=pointer]
            - button "保存源码副本到空间" [ref=e75] [cursor=pointer]
          - iframe [ref=e77]:
            - button "改变预览状态" [ref=f2e2]
        - generic [ref=e78]:
          - paragraph [ref=e79]:
            - text: 基于最近成功版本 v1 修改
            - button "查看修改基准" [ref=e80] [cursor=pointer]
          - generic [ref=e81]: 修改指令
          - textbox "修改指令" [ref=e82]:
            - /placeholder: 描述希望如何修改 HTML…
          - status [ref=e83]: 0/4000 个字符
          - button "生成新版本" [disabled] [ref=e84]
```

# Test source

```ts
  1   | import { expect, test, type Page } from "@playwright/test";
  2   | import { mkdir, writeFile } from "node:fs/promises";
  3   | import { resolve } from "node:path";
  4   | import { login, createConversation, sendMessage, expectReply } from "./helpers";
  5   | import { workFixture } from "../src/components/tasks/taskFixtures";
  6   | test.setTimeout(120_000);
  7   | const evidence = resolve(process.cwd(), "../artifacts/product-acceptance/ui-7/screenshots");
  8   | const sizes = [
  9   |   [360, 800],
  10  |   [390, 844],
  11  |   [768, 1024],
  12  |   [1024, 768],
  13  |   [1280, 800],
  14  |   [1440, 900],
  15  |   [1920, 1080],
  16  | ];
  17  | const artifact = {
  18  |   artifact_id: "ui7-artifact",
  19  |   title: "很长的中文标题和英文路径 / LongTitleWithoutSpaces".repeat(4),
  20  |   kind: "html",
  21  |   conversation_id: null,
  22  |   source_message_id: null,
  23  |   created_at: "2026-10-08T00:00:00Z",
  24  |   updated_at: "2026-10-08T00:00:00Z",
  25  | };
  26  | const version = {
  27  |   artifact_id: artifact.artifact_id,
  28  |   artifact_version_id: "ui7-v1",
  29  |   version: 1,
  30  |   status: "completed",
  31  |   parent_version_id: null,
  32  |   instruction: null,
  33  |   html: "<button onclick=\"this.textContent='保留 iframe 状态'\">改变预览状态</button>",
  34  |   failure: null,
  35  |   created_at: artifact.created_at,
  36  |   started_at: null,
  37  |   completed_at: artifact.created_at,
  38  | };
  39  | const snapshot = {
  40  |   source_kind: "work",
  41  |   run: {
  42  |     source_kind: "work",
  43  |     run_id: "ui7-run",
  44  |     execution_id: "execution",
  45  |     work_id: "ui7-task",
  46  |     requirement_revision: 2,
  47  |     work_control_epoch: 1,
  48  |     conversation_id: null,
  49  |     session_id: null,
  50  |     status: "succeeded",
  51  |     version: 2,
  52  |     created_at: artifact.created_at,
  53  |     started_at: null,
  54  |     finished_at: artifact.created_at,
  55  |     updated_at: artifact.created_at,
  56  |     failure_code: null,
  57  |     failure_message: null,
  58  |     strategy_kind: "generic_agent",
  59  |     executor_key: "general",
  60  |     result_json: { summary: "真实投影结构的测试夹具" },
  61  |     budget: null,
  62  |     branches: [],
  63  |   },
  64  | };
  65  | async function fixtures(page: Page) {
  66  |   let artifactReads = 0;
  67  |   await page.route("**/api/v1/artifacts/ui7-artifact/versions", async (route) => {
  68  |     artifactReads++;
  69  |     await route.fulfill({ json: { artifact, items: [version] } });
  70  |   });
  71  |   await page.route("**/api/v1/runs/ui7-run", (route) => route.fulfill({ json: snapshot }));
  72  |   await page.route("**/api/v1/runs/ui7-run/trace", (route) =>
  73  |     route.fulfill({ json: { run: null, roots: [] } }),
  74  |   );
  75  |   await page.route("**/api/v1/runs/ui7-run/model-inputs", (route) =>
  76  |     route.fulfill({ json: { visibility: "none", items: [] } }),
  77  |   );
  78  |   await page.route("**/api/v1/works/ui7-task", (route) =>
  79  |     route.fulfill({ json: { work: workFixture({ work_id: "ui7-task", title: artifact.title }) } }),
  80  |   );
  81  |   return () => artifactReads;
  82  | }
  83  | async function geometry(page: Page) {
  84  |   const value = await page.evaluate(() => ({
  85  |     viewport: innerWidth,
  86  |     document: document.documentElement.scrollWidth,
  87  |     canvas: document.querySelector(".hp-main-canvas")?.getBoundingClientRect().width,
  88  |     sidebar: document.querySelector(".hp-context-sidebar")?.getBoundingClientRect().width,
  89  |   }));
  90  |   expect(value.document).toBeLessThanOrEqual(value.viewport + 1);
  91  |   if (value.viewport >= 1280) expect(value.canvas).toBeGreaterThanOrEqual(560);
  92  |   const close = page.locator(".hp-inspector .hp-surface-header button").last();
  93  |   if (await close.isVisible()) {
  94  |     const bounds = (await close.boundingBox())!;
  95  |     expect(bounds.x + bounds.width).toBeLessThanOrEqual(value.viewport + 1);
> 96  |     expect(bounds.y).toBeGreaterThanOrEqual(0);
      |                      ^ Error: expect(received).toBeGreaterThanOrEqual(expected)
  97  |   }
  98  |   return value;
  99  | }
  100 | async function screenshot(page: Page, name: string) {
  101 |   await mkdir(evidence, { recursive: true });
  102 |   await page.screenshot({ path: resolve(evidence, name + ".png"), fullPage: true });
  103 | }
  104 | 
  105 | test("seven viewports and boundary resizes keep Artifact iframe, draft and query count", async ({
  106 |   page,
  107 | }) => {
  108 |   await login(page);
  109 |   const reads = await fixtures(page);
  110 |   await page.goto("/#/ai?inspect=artifact:ui7-artifact&version=ui7-v1");
  111 |   await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  112 |   await page
  113 |     .frameLocator('iframe[title="Artifact 预览"]')
  114 |     .getByRole("button", { name: "改变预览状态" })
  115 |     .click();
  116 |   await page.getByLabel("修改指令").fill("保留断点草稿");
  117 |   const initial = reads();
  118 |   const measurements = [];
  119 |   for (const [width, height] of [
  120 |     ...sizes,
  121 |     [1279, 800],
  122 |     [1280, 800],
  123 |     [959, 800],
  124 |     [960, 800],
  125 |     [599, 800],
  126 |     [600, 800],
  127 |   ]) {
  128 |     await page.setViewportSize({ width: width!, height: height! });
  129 |     await expect(page.getByLabel("修改指令")).toHaveValue("保留断点草稿");
  130 |     await expect(
  131 |       page
  132 |         .frameLocator('iframe[title="Artifact 预览"]')
  133 |         .getByRole("button", { name: "保留 iframe 状态" }),
  134 |     ).toBeVisible();
  135 |     measurements.push(await geometry(page));
  136 |     expect(reads()).toBe(initial);
  137 |     if (sizes.some(([w, h]) => w === width && h === height))
  138 |       await screenshot(page, `artifact-${width}x${height}`);
  139 |   }
  140 |   await page.setViewportSize({ width: 1440, height: 900 });
  141 |   await page.getByRole("button", { name: "扩大阅读" }).click();
  142 |   await geometry(page);
  143 |   expect(reads()).toBe(initial);
  144 |   await screenshot(page, "artifact-expanded");
  145 |   await page.emulateMedia({ reducedMotion: "reduce" });
  146 |   expect(
  147 |     await page
  148 |       .locator(".hp-inspector")
  149 |       .evaluate((n) => n.getAnimations().filter((a) => a.playState === "running").length),
  150 |   ).toBe(0);
  151 |   await screenshot(page, "artifact-reduced-motion");
  152 |   await page.addStyleTag({ content: "html { font-size: 32px !important; }" });
  153 |   await geometry(page);
  154 |   await screenshot(page, "artifact-text-200-percent");
  155 |   await writeFile(
  156 |     resolve(evidence, "geometry.json"),
  157 |     JSON.stringify(
  158 |       { initialArtifactReads: initial, finalArtifactReads: reads(), measurements },
  159 |       null,
  160 |       2,
  161 |     ),
  162 |   );
  163 | });
  164 | 
  165 | test("mobile saving suspends the parent; Escape restores draft, iframe and focus", async ({
  166 |   page,
  167 | }) => {
  168 |   await page.setViewportSize({ width: 390, height: 844 });
  169 |   await login(page);
  170 |   const reads = await fixtures(page);
  171 |   await page.goto("/#/ai?inspect=artifact:ui7-artifact&version=ui7-v1");
  172 |   await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  173 |   await page.getByLabel("修改指令").fill("保存覆盖期间保留");
  174 |   const initial = reads();
  175 |   await page.getByRole("button", { name: "保存源码副本到空间" }).click();
  176 |   await expect(page.getByRole("dialog")).toHaveCount(1);
  177 |   await expect(page.locator(".hp-inspector")).toHaveAttribute("hidden", "");
  178 |   await screenshot(page, "mobile-save-layer");
  179 |   await page.keyboard.press("Escape");
  180 |   await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  181 |   await expect(page.getByLabel("修改指令")).toHaveValue("保存覆盖期间保留");
  182 |   await expect(page.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
  183 |   expect(reads()).toBe(initial);
  184 |   await page.keyboard.press("Escape");
  185 |   await expect(page.locator(".hp-inspector")).toHaveCount(0);
  186 |   await expect(page.locator("#canvas-title")).toBeFocused();
  187 | });
  188 | 
  189 | test("all four Inspectors have keyboard tabs, URL replace, geometry and screenshots", async ({
  190 |   page,
  191 | }) => {
  192 |   await login(page);
  193 |   await fixtures(page);
  194 |   const tree = await (await page.request.get("/api/v1/workspace")).json();
  195 |   // The real account's root directory provides File context without fabricated file content.
  196 |   const rootId = tree.root_id;
```