# UI-2 AI 主要交互实施报告

日期：2026-10-07（Asia/Shanghai）。依据 [UI-2 实施计划](ui-2-ai-interaction-plan.md) 与用户补充方案 v1.0，仅实施 UI-2。

## 基线与范围

基线 HEAD：`cc4086273a92ea269abe863b5a054bf5026dcbb1`。开始时已有用户的实施索引修改及未跟踪计划文档；原内容保留。交付为本地未提交改动，未部署。

对应 D01–D04、D07–D10、FE-B1/FE-B3 的既有接线回归与 UI-2 所需查询/命令接线。没有修改后端持久化模型、Run/Work 状态机、SSE、Workspace 权限协议或 Artifact 协议。

## 实现

| 文件 | 变化 |
| --- | --- |
| `web/src/store/workbench.ts` | 对话 cursor 分页、去重与世代隔离；独立 detail 元数据；共享首次创建、冻结 key/payload、未知结果确认、同 Run 不重启 feed、If-Match 改名读回；附件候选 loading/error/去重与迟到清理 |
| `web/src/store/conversationUi.ts`、`sessionLifecycle.ts` | 按账户与对话保存内存草稿、模式和阅读锚点；退出清理；仅淘汰多余空草稿记录，保留非空草稿 |
| `ConversationSidebar.tsx`、`conversation/dateGroup.ts` | 日历分组、已加载标题筛选、分页和首批刷新入口 |
| `conversation/ConversationHeader.tsx` | 标题编辑、冲突保留输入与新版本确认 |
| `ChatPane.tsx`、`adapters/assistant-ui/HpThread.tsx` | 稳定空态 Composer、受控文字、成功才清对应版本、IME、快速/深度、下一条草稿、附件托盘与候选、错误关联、历史锚点与底部跟随 |
| `conversation/ConversationResources.tsx` | 读取选择器、显式目录递归、真实授权 Chips、逐规则撤销与部分失败恢复；保留高级资料管理 |
| `shell/AppShell.tsx`、`store/shell.ts` | 首次创建后的规范路由；附件切换确认及浏览器历史入口保护；普通离开与账户 reset 分开 |
| `api/resources.ts`、`styles.css` | 已知 Office/PDF 后缀 MIME；未知文件不伪装成 text/plain；AI 响应式布局与输入工具栏 |
| 原 App / E2E 测试及新增 UI-2 测试 | 保留旧发送、SSE、权限、保存、下载、Artifact、账户和并发断言；更新 UI-1 已迁移的入口定位 |

已安装 assistant-ui 的 `base-composer-runtime-core.ts` 在提交前清空文字，而 `runtime.onNew` 不消费 boolean 结果，因此局部使用受控 textarea/form，继续复用同一 external-store runtime 渲染消息，不启用 edit/reload/branch。

未知 POST 先读取快照，保留明确的“用原提交确认上次发送”动作。确认只重放原 key 和原 payload；切换页面后成功只清原账户/对话的对应草稿版本。不会通过文本相同推断服务端已提交。

## 环境与命令

前期专用库 `hpagent_ui2_test_20261007`，Redis DB 14；最终全新库 `hpagent_ui2_final_20261007`，Redis DB 13；均采用 migration/API/worker 三角色。API 8182，Vite 5275；最终文件目录 `/tmp/hpagent-ui2-final-files-20261007`。没有清理现有业务库或改变其 Work 状态。凭据未写入报告或日志。

证据列相对 `artifacts/product-acceptance/ui-2/`；完整改动路径见 [文件清单](../../artifacts/product-acceptance/ui-2/changed-files.txt)。

| 命令 | 结果 | 证据 |
| --- | --- | --- |
| `npm run typecheck` | 通过 | `logs/typecheck.log` |
| `npm run lint` | ESLint 与 Prettier 通过 | `logs/lint.log` |
| `npm run build` | 通过；保留产物大于 500 kB 的打包提示 | `logs/build.log` |
| `npm test` | 24 文件、161 测试通过 | `logs/vitest.log` |
| `npm run test:e2e` | 全新隔离库、冻结源码最终 29/29 通过（4.8 分钟） | `logs/e2e-fresh.log` |
| `PYTHONPATH=src .venv/bin/python -m pytest test/web_api/test_workspace_v41_p1.py test/web_api/test_workspace_v41_p3.py -q` | 2 测试通过；一条 FastAPI/Starlette 依赖弃用提示 | `logs/workspace-contract.log` |

最终浏览器范围为全部现有 E2E 与新增 `e2e/ui-2-ai.spec.ts`，真实三角色数据库、Redis 与 Fake Executor，`workers=1`。首轮完整回归 27/28 通过；跨标签竞态用例将输入准备移到启动 Run 之前，并接受同样阻止发送的 queued 状态，显式保留并加强 POST 409 断言。再次在复用库中运行时，Artifact 超时：只读检查发现遗留多个 active reminder/research Work，而新 Artifact Work 停在 ready。Fake Executor 只消费 Artifact executor，其调度前提受测试库遗留工作影响，测试指南已有此限制。停止该失败批次并另建全新测试库，而非改业务协议或调整已有工作状态；原输出保留为 `logs/e2e-reused-database.log`。最后一轮冻结源码，不在 Vite 验收期间修改业务模块。测试集合新增了超过 30 对话的真实分页，因此最终集合为 29 项，历史批次不相加。

单元集成首次创建用例扩展了失败恢复步骤，使用真实 user-event 点击并等待 active ID，单项 timeout 为 10 秒。一次前端检查与浏览器回归同时运行时该用例触发原 5 秒预算；最后单元集合 161 项全通过，不减少业务断言。

## 验收覆盖

| 计划项 | 自动化证据与边界 |
| --- | --- |
| U2-01 | store 分页重叠、刷新/追加乱序、列表恢复；浏览器创建 31 个对象验证真实超过 30 条分页与局部标题过滤 |
| U2-02 | Sidebar 日历边界、加载标题过滤；独立 deep-link detail 与长标题浏览器验收 |
| U2-03 | Header 412 保留输入、If-Match 新版本、PATCH 丢响应读回；浏览器成功改名 |
| U2-04/05 | 首次发送一次创建、创建失败留稿、创建后发送恢复；创建期间离开不发送、不抢导航 |
| U2-06 | 真实 POST 已执行后故障注入丢响应；原 key/payload 确认、无重复消息；真实输入框留稿 |
| U2-07 | 单元和 Chromium composition Enter / 正常 Enter；单元 Shift+Enter。真实输入法人工操作未覆盖 |
| U2-08 | 原策略参数测试；能力入口与运行中锁定；受控输入允许下一条草稿 |
| U2-09/10 | 上传就绪门控、错误与移除、已知文档 MIME、候选去重/请求锁；待发附件切换确认、上传迟到隔离 |
| U2-11/12 | 默认只读与显式递归；授权刷新后才显示；逐规则撤销部分失败/只重试剩余规则；后端授权冻结与停止契约 |
| U2-13 | 60 条历史分页 prepend 浏览器锚点、历史阅读时不跟随流式、回到底部；最多五页自动恢复，缺失位置有继续加载入口 |
| U2-14 | A→B→A 及 AI↔空间草稿；原 runtime/feed 导航测试；Artifact/资料 Surface 生命周期 |
| U2-15 | 原账户迟到隔离测试；新创建、分页、意图 reset；草稿接同一同步 sessionLifecycle 边界 |
| U2-16/17 | 原 runFeed/sseClient/store 回归；真实 disconnect/stop、Markdown、消息附件保存下载、Artifact sandbox 和 Trace 入口 |
| U2-18 | 七尺寸 Chromium、焦点返回、资料 modal、reduced-motion；根字体设置 200% 的布局检查不等于浏览器原生文本缩放审计。实机软键盘及完整人工 WCAG 审计未覆盖 |

## 操作记录与发现

1. 成功：空态输入不创建 → composition Enter 不发送 → 正常 Enter 一次创建并发送 → 流式完成 → 切另一对话并返回恢复草稿。
2. 失败恢复：代理完成真实 POST 后丢弃响应 → 输入保留、提示未知结果 → 原 key/payload 确认 → 两条服务端消息、草稿清空；PATCH 丢响应则 GET 核对标题。
3. 并发/迟到：延迟首次创建响应，切到空间后放行 → 留在空间、不隐式发送 → 新对象进入列表，返回可继续原草稿；账户 reset 使旧请求完全失效。
4. 滚动修复：external runtime 更新消息 DOM 晚于父组件 layout effect，首次 prepend 实测锚点失败；改为监听就绪 DOM 后恢复，并关闭浏览器自身 anchoring。不是通过取消锚点断言绕过。
5. 旧 E2E 存在 UI-1 前八页面入口选择器及 eager-create 假设；更新为三入口和按操作创建，保留原业务断言；资料/账户 modal 与多 status 需要按上下文定位。

空态、流式截图另复跑首次发送场景，记录为 `logs/screenshots.log`，与 29 项集合有重叠，不累计计数。

截图与脱敏日志：[证据目录](../../artifacts/product-acceptance/ui-2/README.md)。

## 后续交接与限制

- UI-3：RunStatus/Trace 仍使用原控制与 Inspector bridge。
- UI-4：高级 WorkspacePanel authority 保留；选择器只承担 AI 的读取授权与失效刷新，不承担空间 controller 重构。
- UI-6：消息 HTML 构建/打开、下载与保存回调保留，Artifact sandbox 仍为 allow-scripts。
- Fake Executor 不证明真实模型、Temporal 或真实渠道行为；实机软键盘、人工输入法、浏览器原生文本放大与完整 WCAG 验收仍需人工完成。UI-2 的自动化结果不构成全量 Shell 生产发布许可。

## 自检发现与修复复验

日期：2026-10-07。修复依据为 [自检修复指南](ui-2-self-review-fix-guide.md)。自检阶段确认 R1–R3 存在，指南记录的三个失败复现属于修复前批次，原 161 项单测、29 项 E2E 与 2 项后端契约结果保持不变，不与本节重跑计数相加。本轮基准仍为 `cc4086273a92ea269abe863b5a054bf5026dcbb1` 上的未提交 UI-2 diff；提交包含原 UI-2 实现与本次修复，文件清单见证据目录。

| 缺陷 | 最终状态 | 修复及复验 |
| --- | --- | --- |
| R1 / U2-11/12 | 复验通过 | 撤销以服务端 grants 读回判断剩余规则，空集合归一为 null，渲染检查首项。DELETE 已执行但响应丢失时关闭弹窗并显示无长期资料；部分剩余只重试仍存在规则，读回失败保留待确认上下文。不从丢失响应推断 Run 停止状态。 |
| R2 / U2-11/12、U2-14 | 复验通过 | 撤销锁按账户会话对象、对话与 operation token 隔离，同步 ref 防重复，state 驱动按钮。finally 释放自身记录不依赖查询 generation；切换主体清理提示，账户变更及卸载阻止旧结果写入。覆盖切走完成、返回后仍 pending、A/B 交错完成、账户重置与卸载。 |
| R3 / U2-06、U2-14 | 复验通过 | Composer 按 conversationKey 保存提交 token，A pending 不阻止 B；A→B→A 防重复，A 成功或异常不释放 B 的锁。保留草稿 revision、IME、运行状态和幂等确认保护，同一个 runtime 持续挂载。浏览器断言真实 POST 的 A/B 对话 ID，并复跑首次创建与原 key 确认。 |

本次修复修改 `ConversationResources.tsx`、`ConversationResources.test.tsx`、`HpThread.tsx`、`HpThread.ui2.test.tsx`、`ui-2-ai.spec.ts`，并将既有 `App.recovery.test.tsx` 的加载恢复集成测试预算调整为 10 秒，保留断言。报告与脱敏日志同步更新；没有改变后端权限、Run、SSE 或 Artifact 协议。

命令均在 `web/` 下执行。以下日志相对 `artifacts/product-acceptance/ui-2/self-review-fix/logs/`。

| 命令 / 批次 | 结果与退出码 | 日志 |
| --- | --- | --- |
| 指南定向 `npx vitest run`（资料、Composer、ui2/workbench/sessionIsolation、App、App.recovery 共 7 文件） | 74 项通过，0 | `targeted-vitest.log` |
| `npm run typecheck` | 通过，0 | `typecheck.log` |
| `npm run lint` | ESLint / Prettier 通过，0 | `lint.log` |
| `npm run build` | 通过，0；保留 >500 kB 提示 | `build.log` |
| `npm test` 最终单独运行 | 24 文件、172 项通过，0 | `test.log` |
| 指南指定六个浏览器文件，新增场景前 | 18 项通过，0 | `e2e-ui2-fix.log` |
| `npm run test:e2e -- e2e/ui-2-ai.spec.ts`，增加三个异常场景后 | 11 项通过，0 | `e2e-ui2-fix-new-scenarios.log` |
| 指南指定六个浏览器文件，补卸载/同步去重保护后 | 21 项通过，0 | `e2e-final.log` |
| `npm run test:e2e -- e2e/ui-2-ai.spec.ts --grep "lost DELETE\|revoke finishing\|B sends"`，最终提示清理实现调整后 | 3 项通过，0 | `e2e-final-three.log` |

六个浏览器文件为 `ui-2-ai.spec.ts`、`conversation.spec.ts`、`multi-tab.spec.ts`、`auth.spec.ts`、`workspace-p1.spec.ts`、`workspace-p3.spec.ts`。各批次有重叠，分别记录不累计。相较原 161 项单测，最终集合新增 11 项；浏览器专项新增 3 项。

Chromium 使用独立 PostgreSQL 三角色与 Fake Executor、单 worker。各批次专用库 `hpagent_ui2_fix_20261007`、`hpagent_ui2_fix2_20261007`、`hpagent_ui2_fix3_20261007`、`hpagent_ui2_fix4_20261007`，Redis DB 分别为 12/11/10/9；API/Vite 分别为 8184/5277、8185/5278、8186/5279、8187/5280，文件根目录分别为 `/tmp/hpagent-ui2-fix-files-20261007` 及对应 `fix2`/`fix3`/`fix4` 目录。没有清理业务库，凭据未写入日志。

过程失败保留：新增浏览器批次首次启动遗漏本地 migration 路径，服务启动前退出 1，补环境变量后通过（`e2e-setup-failure.log`）；新增提示清理 effect 触发 React lint 规则，改为按主体变化清理局部状态后通过（`lint-previous.log`）；单测与 Chromium 并行时两项 App 集成测试超出 5 秒，最终单独复跑（`test-concurrent-timeout.log`）。此前加载恢复测试也曾触发 5 秒预算，单项复跑通过后调整为 10 秒。

原限制继续保留：实机软键盘、真实 IME、原生文本缩放、完整人工无障碍及真实模型/provider 行为仍未由本轮自动化证明。
