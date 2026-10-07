# UI-3 执行与诊断实施报告

日期：2026-10-07（Asia/Shanghai）。基准 HEAD：`325a5d7d1282834dc7cd9998f05674119e23b81e`。

依据 [UI-3 计划](ui-3-execution-diagnostics-plan.md) 和用户补充的《HpAgent 前端 UI 重构方案 v1.0》实施。附件作为设计参考；本次用户授权执行 UI-3，不扩展至 UI-4～UI-8。初始工作区已有实施索引修改和未跟踪的 UI-3 计划，均予以保留。未改变后端状态机、数据库迁移、SSE 协议或产品依赖。

## 实现

- `HpThread → ExecutionBlock → RunStatus`：原始 assistant `message_id / produced_by_run_id` 绑定；消息尚未存在时使用唯一临时占位，不创建消息。历史引用只提供详情入口，不推断完成状态。完成、失败、停止的符号分开；停止和重试继续使用 Workbench 的资格及命令锁；点击时再次核对 Run ID，旧按钮不能操作新选中的 Run。断线文案改为同步状态；非法排队时间不创建计时。Token/模型请求明细移入高级诊断的 RunBudget，保留原使用、预留、估算和无法计量的断言。
- `InspectorHost → RunInspector`：唯一 Surface 内提供概览、使用资料与输出、高级诊断。当前 Chat 复用 Workbench；历史 Chat/Work 独立查询，取消旧视图查询并使用选择世代和请求 token 排除迟到结果。独立非终态递归轮询、错误退避、隐藏暂停；终态停止周期 snapshot 请求。Work 不读取 assistant_message、不写入 Chat activeRun；主动作回任务页并定位真实 WorkPanel 行。
- 资料、输出：候选串行分页、node_id 去重、视图失效后禁止追加；展示 fixed/read 的差别及 API Gap。输出下载使用 file_id/content；保存按需获取元数据并复用现有 SaveWorkspaceDialog。切走时迟到元数据不打开保存对话框；真实候选 node_id 可打开 File Inspector 并经返回栈回到 Run；没有真实 node_id 时不冒充 File Inspector 引用。
- 审批：已有 GET/approve/reject API wrapper、真实时间字段、六状态显示。允许/拒绝共享 approvalId 意图锁；首次 key 冻结，关闭/切 Run 不丢命令。响应丢失先读回，仍 pending 且有效时只允许重放原 decision/key；409 禁止重放；到期以服务端结果为准。approved/consumed 的文案不宣称业务操作成功。提交成功使此前审批 GET 失效；点击时再核对选中 Run 与已知审批状态，旧按钮不能反向决策。会话重置清除所有意图。
- Trace：预算刷新改为当前 feed 的有界 nodeId→type 记录，不再依赖查看中的树。只将匹配且已激活的事件送入 Trace；终态只同步匹配对象。GET token、有界在途事件重放、连续手动刷新缓冲延续及终态不倒退共同保护快照竞态；溢出重取，403/404 清树和模型正文。未知诊断状态不显示成功标记。
- Model Input：补 Run 列表 wrapper，区分 none 的最小投影与 summary/full_safe。无 Trace 仍可按需查看模型记录；列表同步/重新进入/权限拒绝清正文并递增模型查询世代，迟到正文不回灌。沿用安全文本/pre 渲染，不把输入放入 URL、本地存储或普通日志。
- 导航：Shell 存最小 tab/origin，页签不改变 URL。键盘左右/Home/End、命名 tabpanel、Surface 焦点和 Esc 保留；开关 Inspector 不重新创建聊天 runtime。WorkPanel 增真实 coordinator Run 入口及任务行定位。

D01/D03/D07/D08/D10：沿用三入口和唯一 Shell/Surface、真实状态与权限、响应式和焦点规则。FE-B3：历史选择、快照竞态及预算依赖修复；FE-B1：账户清理和迟到响应保护。

## 入口迁移

| 原入口/行为 | 新入口/行为 |
| --- | --- |
| ChatPane 顶部 RunStatus | 所属消息 ExecutionBlock；无消息时临时占位 |
| ChatPane 单独 Trace 按钮 | 当前/历史消息的“查看执行详情”→高级诊断 |
| InspectorHost Run snapshot + TracePanel | RunInspector 单独负责 snapshot；TracePanel 只负责诊断内容 |
| Work coordinator ID 文本 | WorkPanel 的“查看执行详情” |
| 历史 Work 手工查询 | 继续保留任务页 RunLookup，经 Shell 打开同一个 Inspector |
| 历史 Chat 停止/重试 | 返回所属对话，经原 snapshot/资格确认后操作，不直接调用另一 activeRun 的命令 |

兼容调用点：Workbench `followRun` 只更新关闭状态的提示；`traceStore.setOpen(true)` 保留单向 Shell bridge。`setOpen(false)` 不反向关闭其他 Inspector。TestPages 中未被主 Shell 挂载的 DiagnosticsPage 保留为旧开发页，UI-7 统一清理；当前主界面没有第二个诊断面板或诊断一级入口。

## 验证环境与结果

结果见 [证据目录](../../artifacts/product-acceptance/ui-3/README.md)。使用独立可丢弃数据库 `hpagent_ui3_test_20261007`（后端契约）与 `hpagent_ui3_e2e_20261007`（浏览器）；真实 migration/API/worker 三角色，Redis DB 13，文件目录 `/tmp/hpagent-ui3-files-20261007`，API 8183 / Vite 5276。凭据只保存在临时权限 0600 环境文件，不进入证据。

最终源码全量单测 **197/197 通过（30 个文件）**；`typecheck`、`lint`、`build` 均正常退出 0。受影响浏览器回归稳定批次 **28/28 通过**（含 2 条 UI-3 场景）；审批 API/持久化 **8/8**、Model Input/Work API **6/6** 正常退出通过。最终源码 UI-3 浏览器 **2/2 通过，exit 0**（与 28 项批次重叠，不累计）；Run/Workspace API/持久化 **31/31 通过，exit 0**；三个后端批次合计 **45 个不同用例通过**，不与此前 SIGTERM 的综合批次重复累计。后端保留 Starlette/httpx 弃用提示，未升级依赖。构建仍提示既有主 bundle 超过 500 kB；不影响构建成功，本阶段未做全局拆包。

已知首轮问题：对象不可用状态缺少原返回按钮（已恢复）；格式检查遇到新增文件未格式化（已格式化）。初次浏览器启动在后端契约仍运行时被主动中断，随后为浏览器建立另一独立数据库重新执行；中断启动不计作通过。

## U3 验收映射

| 编号 | 可复验证据 |
| --- | --- |
| U3-01 | HpThread 原始 ID 映射、ExecutionBlock 历史未知状态单测、真实 API 消息位置 E2E |
| U3-02 | RunStatus 安全重试/停止/排队测试、终态符号单测、stop-retry E2E |
| U3-03 | 原 runFeed/sseClient/Workbench 全量测试及 disconnect E2E |
| U3-04 | 原 App/App.recovery/HpThread UI-2 测试；Inspector 关闭后草稿、焦点 E2E |
| U3-05 | Work 型 fixture、独立 snapshot 单测、Work 无 assistant 的浏览器夹具 |
| U3-06 | 历史块没有 stop/retry；返回所属对话由原 Workbench 加载与资格约束 |
| U3-07 | RunInspector A→B→A/reset/abort、既有 sessionIsolation、审批关闭与迟到测试 |
| U3-08 | Workbench 选中 historical-B 时 A 的 LLM 预算刷新断言；B 树不变 |
| U3-09 | Trace GET 重放、终态不倒退、同 Run 竞争刷新、续存缓冲测试 |
| U3-10 | 无 Trace 404 + Model Input 列表浏览器夹具；局部失败可刷新 |
| U3-11 | 原 TraceDetail none/summary/full_safe 测试、模型正文刷新世代单测、后端 observability 契约 |
| U3-12 | RunResources 串行分页/去重及旧视图失效保护；既有 Workspace 权限契约 |
| U3-13 | terminal resources 404 单测、真实 API 终态限制 E2E |
| U3-14 | 迟到文件元数据不触发保存单测；既有 workspace-p1/p3/artifact 资源链回归 |
| U3-15 | 409/到期/互斥、旧审批查询与旧按钮保护单测；pending/approved 浏览器夹具；真实审批 API/持久化契约 |
| U3-16 | 同 key 重放、切走重开、reset 单测；POST 处理后响应丢失再读回夹具；原 ApiClient CSRF 测试 |
| U3-17 | 可见非终态递归轮询、页面隐藏/关闭清定时器；不打开诊断时不查询的单测；未声称覆盖所有浏览器节流策略 |
| U3-18 | 原 Shell/App 路由和不可用测试；Run→真实候选 File→Run 返回栈单测；手机 modal/关闭焦点、Work 返回页 E2E |
| U3-19 | 七视口布局与截图、键盘 Tabs、reduced-motion；200% 文本缩放、Tree 人工键盘及实机软键盘仍需人工验收 |
| U3-20 | 既有 UI-2 全量单测、ui-2-ai/conversation/auth/multi-tab E2E |

## 已知边界与后续交接

- **UI-3 API Gap：历史候选读取**。终态、cancelling、未 ready 或撤权可能返回 resources 404。界面明确不代表未使用资料，不以当前 grants/全空间拼接历史；先前会话缓存不承诺刷新后恢复。UI-4 若需完整回放，应单列后端增强。
- UI-5：Work 结束不等于 Task completed；沿用 WorkPanel 控制，不新增任务四桶/完整 TaskInspector。当前 Work 在已有列表中时可见定位；列表尚未包含对象时不伪造该 Work 的控制数据。
- UI-6：输出 file_id 不等于 node_id；按已有文件保存链执行，不自动授权。Artifact 闭环仍用现有实现。
- 未验证真实 Provider、QQ 渠道或 Temporal 审批消费/工具恢复。浏览器丢响应与 Work 诊断场景是标注的网络夹具，不证明真实副作用发生。当前会话无权限变更推送，清正文发生在重新进入/刷新/拒绝与账户边界，不承诺瞬时撤回。
- 完整 WCAG 2.2 AA、200% 文本缩放、实机 IME/软键盘及人工 Tree 操作未认证；自动化布局和焦点证据不替代这些人工门槛。UI-3 实施不等于 UI-8 发布验收。

回退只回退本批前端接线和视图，保留服务器审批和 Work 状态；不逆转审批、不删除用户数据。
