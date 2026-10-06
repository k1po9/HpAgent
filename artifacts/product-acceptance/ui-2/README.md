# UI-2 AI 交互验收证据

日期：2026-10-07（Asia/Shanghai）。实现与覆盖矩阵见 [UI-2 实施报告](../../../docs/implementation/ui-2-ai-interaction-report.md)。

基线 HEAD：`cc4086273a92ea269abe863b5a054bf5026dcbb1`。开始时已有用户修改：`docs/implementation/README.md`，以及未跟踪的 `docs/implementation/ui-2-ai-interaction-plan.md`；保留原内容并补报告索引。

- `logs/`：前端检查、Chromium 回归和后端权限契约命令输出，失败后的修复重测独立记录。
- `screenshots/`：七种视口的 AI 页面，以及资料弹窗、长历史、附件失败和文字放大。
- `self-review-fix/logs/`：R1–R3 修复后的定向/全量单测、静态检查、浏览器故障注入与回归，以及过程失败日志；各批次分别计数。
- Chromium 使用真实隔离 API / PostgreSQL / Redis / 文件服务和 Fake Executor；历史滚动场景仅替换历史消息 GET，运行与 SSE 仍使用真实 API。
- 截图与模拟 IME 事件不能代替实机软键盘、真实中文输入法或人工无障碍审计。
