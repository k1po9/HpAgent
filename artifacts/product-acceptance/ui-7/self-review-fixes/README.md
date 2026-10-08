# UI-7 自检修复证据

日期：2026-10-08。修复 R1/R2/R3，范围及交接见[修复记录](../../../../docs/implementation/ui-7-self-review-fixes.md)。原独立自检与失败证据保留于相邻 [self-review](../self-review/README.md)，原实施批次证据保持。

## 本轮批次

| 批次 | 结果 | 日志 |
| --- | --- | --- |
| 相同原始三项反例，修复前 | 3 失败，exit 1 | [repro-before.log](repro-before.log) |
| 相同原始三项反例，修复后 | 3 通过，exit 0 | [repro-after.log](repro-after.log) |
| 扩展 fixture Chromium | 12 通过，exit 0 | [browser-second.log](browser-second.log) |
| 完整前端检查 | typecheck / lint / build 均 exit 0；Lint 无警告 | [frontend-exits.json](frontend-exits.json) 与对应 *-final.log |
| 完整 Vitest | 54 文件 / 405 项通过，exit 0 | [unit-final.log](unit-final.log) |
| 综合 Chromium | 74 / 74 通过，exit 0，15.0 分钟；已包含 12 个新场景 | [e2e-final.log](e2e-final.log) |

`browser-first.log` 区分第一次真实键盘边界问题与测试 locator/fixture 假设错误；`targeted-after.log` 中 3 个失败是参数化测试的 Zustand spy 清理问题，`trace-isolation-fixed.log` 记录 6 项复验通过。`before-resize-*` 为补充响应式导航意图反例前的前端通过批次（54 文件/404 项），不作为最终结果或与后续计数相加。

## 复测

```sh
# 在 web 目录；只起 Vite 5288，API 全部响应 fixture
npx playwright test --config=playwright.ui7-review.config.ts

# 完整前端检查
npm run typecheck
npm run lint
npm run build
npm test -- --maxWorkers=2
```

综合 Chromium 用默认 Playwright 配置，包含上述新 spec 与原 UI-7 受影响 20 个 spec，采用全新专用数据库。真实 API、测试执行器及 fixture 的作用不同，报告逐项说明；不将合成 SSE 当作真实后端发送。

浏览器 JSON/截图在 [browser](browser)，其中 R3 的 before/after 是初始恢复稳定后的请求差量：StrictMode 初始诊断请求不假设固定为一次，终态只允许当前高级诊断增加一次；普通/概览/历史/关闭增加零次，权威 Run GET 和最后消息仍到达。

本目录的 [verification-summary.json](verification-summary.json) 记录独立退出码；[source-fingerprints.json](source-fingerprints.json) 为 64 项前端文件状态/指纹，[repair-files.json](repair-files.json) 为相对独立自检的 14 项修复文件；[old-evidence-audit.json](old-evidence-audit.json) 确认原 148 项实施证据、16 项独立自检证据保持。[evidence-sha256.json](evidence-sha256.json) 单独记录本轮截图/JSON 内容哈希。

本轮截图：新 fixture 场景在 browser；原 UI-7 6 项生成的截图/测量在 unification-regression；其它既有业务本轮截图在 regression-evidence，历史文件恢复，见 regression-evidence-map.json。API8187 / Vite5280 / fixture Vite5288 无残留监听。原未覆盖人工验收和全量 timer/runtime/feed 峰值范围不因修复标记通过。
