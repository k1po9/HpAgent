# UI-3 自检修复证据

日期：2026-10-07（Asia/Shanghai）。修复基准 `d577c36`。范围与因果说明见 [自检修复记录](../../../../docs/implementation/ui-3-self-review.md)。

原审查证据：`existing-targeted.log`、`repro.log` 和 `ui3-review-repro.test.tsx.txt` 保留当时结果。新增 `polling-before-fix.log` 记录 Trace 重复定时链的失败断言。

修复后定向命令（web/）：

```bash
npx vitest run src/components/run/RunInspector.test.tsx src/components/run/RunResources.test.tsx src/store/runInspector.test.ts src/components/trace/traceStore.test.ts src/components/trace/TraceDetail.test.tsx src/store/sessionIsolation.test.ts --maxWorkers=1
```

最终命令（web/；单测与浏览器串行）：

```bash
npm test -- --maxWorkers=1
npm run typecheck
npm run lint
npm run build
npm run test:e2e -- e2e/ui-3-execution.spec.ts
```

| 验证 | 结果 | 日志 |
| --- | --- | --- |
| 定向 Run/会话隔离/Model Input | 42/42，6 文件，exit 0 | [fixes-targeted.log](fixes-targeted.log) |
| 全量 Vitest | 208/208，30 文件，exit 0 | [fixes-unit.log](fixes-unit.log) |
| TypeScript | exit 0 | [fixes-typecheck.log](fixes-typecheck.log) |
| ESLint / Prettier | exit 0 | [fixes-lint.log](fixes-lint.log) |
| 生产构建 | exit 0；既有主 bundle >500 kB 提示 | [fixes-build.log](fixes-build.log) |
| UI-3 Chromium | 3/3，exit 0 | [fixes-e2e.log](fixes-e2e.log) |

定向批次与全量单测重叠，不累加用例数量。浏览器采用隔离库 `hpagent_ui3_e2e_20261007`、Redis DB 13、API 8183、Vite 5276、Fake Executor，workers=1；R1/R2 新场景为明确标注的网络夹具，仅使用合成文件/模型正文。凭据不记录。

本批未重跑历史 28 项浏览器和 45 项后端契约；未认证 Temporal/真实 Provider/QQ、完整 WCAG 或实机软键盘。沿用原报告的能力边界。截图 `terminal-output-refresh.png` 展示合成终态输出；`model-permission-denied.png` 展示拒绝后无正文、经新请求恢复 summary。
