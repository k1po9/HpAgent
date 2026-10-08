# UI-7 独立自检证据

日期：2026-10-08（Asia/Shanghai）。被审对象为 HEAD `e6791e3` 上的未提交 UI-7 实现。
结论与修复门槛见 [自检记录](../../../../docs/implementation/ui-7-self-review.md)：R1/R2/R3 均为 P2，待修复。

| 文件                                                                                                                                                         | 说明                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| [baseline-source-sha256.json](baseline-source-sha256.json)                                                                                                   | 自检开始时 56 项前端源码/测试路径、状态及 SHA-256；结束时复核一致                     |
| [evidence-audit.json](evidence-audit.json)                                                                                                                   | 原 148 项归档文件无缺失、无哈希不匹配                                                 |
| [targeted-existing.log](targeted-existing.log)                                                                                                               | 12 文件/87 项既有定向 Vitest，exit 0                                                  |
| [UI7SelfReview.repro.test.tsx.txt](UI7SelfReview.repro.test.tsx.txt)                                                                                         | 3 个失败反例原源文件，运行后从 web/src 移出，避免进入正常套件                         |
| [repro-unit.log](repro-unit.log)                                                                                                                             | 新反例 3 项全部失败，exit 1，分别对应 3 个缺陷                                        |
| [browser-repro.mjs](browser-repro.mjs)、[browser-repro.json](browser-repro.json)、[browser-repro.log](browser-repro.log)                                     | 真实 Chromium 操作当前前端，用响应 fixture 验证 R1/R2；记录 modal 消失和 Tab 焦点序列 |
| [trace-browser-repro.mjs](trace-browser-repro.mjs)、[trace-browser-repro.json](trace-browser-repro.json)、[trace-browser-repro.log](trace-browser-repro.log) | 当前前端接收合成 SSE 终态，确认没有 Inspector 仍发 Trace GET                          |
| [R1 截图](R1-late-lookup.png)、[R2 截图](R2-inbox-keyboard.png)、[R3 截图](R3-ordinary-chat.png)                                                             | 三个场景；R1 截图可能处于 FLOW 视觉过渡，问题以 modal/DOM 记录为准                    |

浏览器脚本的 exit 0 表示成功记录缺陷，不是业务通过。使用本地 Vite 5288，全部 API/SSE 为浏览器响应 fixture；未启动真实后端/数据库，记录的请求均为 GET，pageErrors 为空。服务结束后停止；此批不能代替真实 API、生产模型、Temporal、渠道或人工无障碍验收。

既有定向命令在项目根执行：

```bash
npm --prefix web test -- --maxWorkers=2 src/components/shell/Surface.test.tsx src/components/shell/Assembly.test.tsx src/components/shell/useInspectorFlow.test.tsx src/components/artifact/ArtifactCreateForm.test.tsx src/components/run/RunLookup.test.tsx src/store/shell.test.ts src/store/sessionIsolation.test.ts src/components/trace/traceStore.test.ts src/components/run/RunInspector.test.tsx src/components/run/RunResources.test.tsx src/components/workspace/WorkspaceMigration.test.tsx src/App.recovery.test.tsx
```

反例运行时文件名为 `web/src/UI7SelfReview.repro.test.tsx`，命令为：

```bash
npm --prefix web test -- --maxWorkers=1 src/UI7SelfReview.repro.test.tsx
```

复现时将 `.txt` 内容放回上述临时路径；修复时应将相应测试归入正式组件/Store 回归并加强端到端前景层及键盘验证。本次没有为了让反例变绿修改实现。
