# UI-7 实施证据（2026-10-08）

基线 `e6791e35990ce6da811f6204806def202e770076`。执行范围和 A7-01～A7-26 的逐项状态见[实施报告](../../../docs/implementation/ui-7-unification-cleanup-report.md)；能力和旧测试删除映射见[迁移映射](migration-map.md)。不将自动化通过等同完整 UI-7 人工验收。

## 最终验证

| 验证 | 结果 | 日志 |
| --- | --- | --- |
| `npm run typecheck` | 通过，退出 0 | [typecheck-final.log](logs/typecheck-final.log) |
| `npm run lint` | 通过，退出 0 | [lint-final.log](logs/lint-final.log) |
| `npm run build` | 通过，退出 0；保留 Vite 大 chunk 提示 | [build-final.log](logs/build-final.log) |
| `npm test -- --maxWorkers=2` | 53 文件 / 391 用例通过，退出 0 | [unit-final.log](logs/unit-final.log) |
| UI-7 Chromium 定向 | 6 / 6 通过，退出 0 | [e2e-ui7-verified.log](logs/e2e-ui7-verified.log) |
| 最终受影响 Chromium 综合回归 | 62 / 62 通过，退出 0；15.2 分钟 | [e2e-regression-verified.log](logs/e2e-regression-verified.log) |

前端命令和退出码见 [frontend-exits.json](logs/frontend-exits.json)，总结果见 [verification-summary.json](logs/verification-summary.json)。综合 62 项已包含 UI-7 的 6 项，不同批次覆盖重复用例，不相加为独立测试数；首轮受影响回归 `e2e-regression.log` 为 60 通过 / 1 失败（新增 Task 用例定位器问题），其余既有业务用例通过。随后修正定位器并复查层级竞态，最终六项 UI-7 定向全部通过。最终综合复验曾出现 57 通过 / 5 个 HTML 等待超时，原因是重复测试库的 queued Reminder/Research 占满协调容量，见[调查](logs/capacity-investigation.md)与保留的 `e2e-regression-capacity-blocked.log`；随后换全新隔离库完整 62 项全部通过。早期失败日志保留，不能当作当前失败状态。

最终受影响综合批次在 `web` 目录运行以下 20 个 spec（worker=1，真实 API、测试执行器）：

```sh
npx playwright test \
  e2e/ui-7-unification.spec.ts e2e/auth.spec.ts e2e/a11y.spec.ts \
  e2e/multi-tab.spec.ts e2e/disconnect.spec.ts e2e/stop-retry.spec.ts \
  e2e/ui-2-ai.spec.ts e2e/ui-3-execution.spec.ts e2e/ui-4-workspace.spec.ts \
  e2e/ui-5-tasks.spec.ts e2e/ui-5-recovery.spec.ts e2e/artifact.spec.ts \
  e2e/ui-6-artifact.spec.ts e2e/ui-6-self-review.spec.ts \
  e2e/conversation.spec.ts e2e/markdown.spec.ts e2e/manual-repair.spec.ts \
  e2e/workspace-direct-upload.spec.ts e2e/workspace-p1.spec.ts \
  e2e/workspace-p3.spec.ts --project=chromium
```

## 环境与证据解释

- 初轮专用 PostgreSQL 数据库 `hpagent_ui7_e2e_20261008`，最终综合批次用新库 `hpagent_ui7_final_20261008` 和新文件目录 `/tmp/hpagent-ui7-final-files-20261008`；migration/api/worker 各用原角色访问。API 8187、Vite 5280、Redis DB 11。实际连接凭据仅在启动环境中传递，报告不保存凭据。测试没有使用业务数据库或修改后端协议；复测综合批次需提供全新的专用数据库环境，以免测试执行器不处理的 Reminder/Research 累计占满容量。
- 前端完整检查串行执行；Vitest 限制为两个 worker。初轮并发的机器内存争用日志保留在 `unit-full-initial.log` 等，最终失败均有后续复验，不降低断言。
- 原 UI-2～UI-6 spec 的固定截图输出复制到 [regression-evidence](regression-evidence)，再恢复历史文件。映射见 [regression-evidence-map.json](logs/regression-evidence-map.json)。本轮新截图直接写入 [screenshots](screenshots)：新截图 42 张，业务回归归档截图 106 张；内容 SHA-256 见 [evidence-sha256.json](logs/evidence-sha256.json)。测试服务自动退出，8187 / 5280 无残留监听。
- [baseline.md](logs/baseline.md) 记录实际开始时的 HEAD / 用户文档变更；[changed-files.json](logs/changed-files.json) 列源码与文档修改/删除；[static-scans.json](logs/static-scans.json) 记录最终旧组件/两个导航 bridge/旧 CSS 的精确 rg 命令及无匹配结果；[deleted-css-selectors.txt](logs/deleted-css-selectors.txt) 补充死选择器清单。

## 布局、状态与交互

- 七个视口：360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080。四类 Inspector 的 ready 状态截图均保留；另有默认/扩大阅读、长中英标题、根字号 200%、reduced-motion、未知 Run 404、保存/收件箱/任务编辑的移动前景层截图。
- [geometry.json](screenshots/geometry.json) 记录七视口及 1279↔1280、959↔960、599↔600 实时 resize 的页面/Canvas/Inspector 几何。浏览器额外断言 iframe 内状态和草稿保持、初始/最终 Artifact 列表 GET 各为一次。
- [a11y-measurements.json](screenshots/a11y-measurements.json) 为任务 Inspector 选中/未选中 Tabs 的实际计算色：约 6.00:1 / 5.95:1；移动关闭按钮为 44×44px，四个 Tabs 高度 44px。只代表这些明确测量控件，不推导全站色对合规。
- [motion-controls.json](screenshots/motion-controls.json) 记录发送、停止、终态后同一 DOM 控件及相同包围盒。显式登录视觉层出现一次、800ms 后移除；刷新恢复不再次播放。
- [操作记录](flows/README.md) 区分真实 API、网络故障注入与延迟 Promise；新七视口对象使用真实登录后的网络 fixture，不能冒充真实后端产出的全部对象。Header 创建、Run 404、登录/发送/停止与既有领域回归走真实 API。

## 尚未完成的人工验收

真机软键盘和非零安全区、实际屏幕阅读器、全产品 200% 文本逐流程、所有 muted/disabled/focus/边界色对、所有前景层交叉组合及全量 timer/runtime/feed 峰值仪表仍需补验。完整阶段退出不能由此目录的浏览器截图推导。真实模型、Temporal 和真实通知渠道的综合验证按 UI-8 / BE 范围交接。
