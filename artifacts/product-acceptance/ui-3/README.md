# UI-3 验收证据

2026-10-07（Asia/Shanghai）；基准 `325a5d7`。实现说明与 U3-01～U3-20 映射见 [实施报告](../../../docs/implementation/ui-3-execution-diagnostics-report.md)。

修复批次的 R1/R2、轮询补验和最终验证见 [自检与修复记录](../../../docs/implementation/ui-3-self-review.md) 与 [修复证据](self-review/README.md)。本文件原统计保留为 `d577c36` 历史证据。

## 环境

后端契约库：`hpagent_ui3_test_20261007`；浏览器库：`hpagent_ui3_e2e_20261007`。API / worker / migration 三个 DSN 指向对应独立库；Redis DB 13、API 8183、Vite 5276、独立文件目录 `/tmp/hpagent-ui3-files-20261007`。浏览器 workers=1；前端 Vitest 最终批次 maxWorkers=1，控制本机内存与 CPU 竞争。凭据不收录；复验按 [测试指南](../../../docs/development/testing.md)配置对应隔离环境。

## 命令与记录

```bash
# web/：静态与全量单测
npm test -- --maxWorkers=1
npm run typecheck
npm run lint
npm run build

# web/：真实 API / PG / Redis / Fake Executor + 两条 UI-3 场景
npm run test:e2e -- e2e/ui-3-execution.spec.ts e2e/disconnect.spec.ts e2e/stop-retry.spec.ts e2e/conversation.spec.ts e2e/ui-2-ai.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts e2e/workspace-p1.spec.ts e2e/workspace-p3.spec.ts e2e/artifact.spec.ts

# 根目录：聚焦真实持久化/API 契约（分批串行使用契约库）
PYTHONPATH=src .venv/bin/python -m pytest test/test_file_action_approval_contract.py test/web_persistence/test_file_action_approvals.py -q
PYTHONPATH=src .venv/bin/python -m pytest test/web_api/test_model_observability.py test/web_api/test_work_foundation.py -q
PYTHONPATH=src .venv/bin/python -m pytest test/web_api/test_phase_b_api.py test/web_api/test_workspace_v41_p1.py test/web_persistence/test_workspace_v41_p1.py -q
```

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| 原实施全量 Vitest | 197/197，30 个文件，exit 0 | [unit.log](logs/unit.log) |
| TypeScript | exit 0 | [typecheck.log](logs/typecheck.log) |
| ESLint / Prettier | exit 0 | [lint.log](logs/lint.log) |
| 生产构建 | exit 0；主 bundle >500 kB 提示 | [build.log](logs/build.log) |
| 受影响 Chromium 回归 | 28/28，exit 0；最后 Run ID 保护前的稳定批次 | [e2e-regression.log](logs/e2e-regression.log) |
| 原实施 UI-3 复验 | 2/2，exit 0；含最后审批查询竞态修复 | [e2e-ui3.log](logs/e2e-ui3.log) |
| 审批 API / 持久化 | 8/8，exit 0 | [approval-contract.log](logs/approval-contract.log) |
| Model Input / Work API | 6/6，exit 0 | [model-work-contract.log](logs/model-work-contract.log) |
| Run / Workspace API / 持久化 | 31/31，exit 0 | [run-resource-contract.log](logs/run-resource-contract.log) |

后三个后端批次互不重叠，共 45 个不同用例正常退出通过；浏览器 UI-3 复验与 28 项批次重叠，不累计为 30 项。

## 三类操作记录

1. **成功 / 真实 API**：创建对话→发送合成验收内容→assistant 消息位置出现执行块→真实终态→打开概览（无额外 snapshot GET）→资料页准确说明终态接口限制→高级诊断→关闭回触发按钮→草稿不丢→手机 modal/Esc。对应 UI-3 E2E 第一条；现有 stop-retry/disconnect 另验证停止及断线恢复。
2. **失败恢复 / 网络夹具**：合成 Work Run→pending 文件操作审批→approve 夹具更新服务端模拟状态但中断响应→GET 读回 approved→显示“已授权，等待执行状态更新”→拒绝按钮不再出现→POST 计数为 1。对应 UI-3 E2E 第二条；真实审批持久化/幂等由 approval-contract.log 验证，不能把夹具推导成真实工具消费。
3. **并发 / 迟到**：A GET 未返回→切 B→再选 A→新快照先返回→旧 A 返回不覆盖；账户 reset 后迟到响应不写回。Trace GET 期间 start/end/旧 start→终态和 token_usage/snapshot_id 保留；第二次手动刷新保留先前在途事件。审批连点允许/拒绝只创建同一意图，未知结果核对后复用同 key；关开面板不允许相反决策。对应 RunInspector/TraceStore/Workbench/资源保存单测。

## 图像

UI-3 专属 PNG 均为测试账户或明确合成脱敏数据。`advanced-360x800` 至 `advanced-1920x1080` 共七尺寸；另包括 pending 审批、消息执行、Chat 概览、终态候选接口限制、手机面板和对象不可用。

截图说明布局及可见状态，不证明实机软键盘、完整 WCAG 或真实模型工具恢复。Trace 404 场景及模型摘要列表使用浏览器路由夹具；模型正文不来自真实请求。

## 失败批次与修复

- 第一轮单测 179/180：对象不可用时缺原返回按钮，已恢复。
- 追加测试过程中曾有格式警告和测试类型错误（Testing Library 不接受 Playwright 的 `exact` 参数），已修正。
- 并行开发检查曾出现 App 测试超时；稳定源码的 maxWorkers=2 批次曾通过 193 项。最终新增 2 项点击时 Run ID 保护后，单测与浏览器并发复验再次超时（14 个单测、2 个浏览器用例）；保留失败日志并串行复验：195/195 单测、2/2 UI-3 浏览器正常退出通过；后续审批竞态补充后的最终结果为 197/197 单测、2/2 浏览器。测试过程曾暂停契约测试进程以避免资源竞争，随后继续；不调整应用或测试的超时门槛。
- 首轮浏览器 26/28：发送恢复流程失败、键盘 Tabs 焦点用例失败。Tabs 改为从真实焦点页签计算方向；发送恢复流程在停止源码修改后复测。不中途修改应用代码的最终批次作为验收结果。
- 首次综合后端输出 45 passed；执行会话最终返回 SIGTERM/143，因此不将该批记为正常退出验收。后续拆分三个批次，记录各自显式退出码；这些与综合批次重复，不累计为新增用例。
- 最终审批检查补充了 POST 成功后废弃旧 pending 查询，以及旧按钮核对选中 Run/已知状态的防护；新增迟到查询与切 Run 两个单测。
- 最初浏览器准备曾被主动中断，随后使用独立浏览器库，避免与后端 fixture 清表交叉。不计作测试通过。

## 未覆盖

Temporal 审批消费/唤醒、真实 Provider/QQ；完整 WCAG 2.2 AA、真实系统文本缩放、人工 Tree 键盘和实机输入法/软键盘。终态候选资料完整历史回放是后端 API Gap，页面如实限制，不记为完整回放通过。
