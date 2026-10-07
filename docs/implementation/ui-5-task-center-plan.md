# UI-5 任务中心实施计划

> 状态：规划基线（制定日期：2026-10-07，Asia/Shanghai）；后续实施结果见 [UI-5 实施报告](ui-5-task-center-report.md)。本文“本次/未执行”等表述保留计划制定时的语义。
> 本文以《HpAgent 前端 UI 重构方案 v1.0》为产品基准，结合 UI-4 完成后的真实代码制定实施步骤；不代表 UI-5 已实现或测试已通过。

## 1. 基准与交付边界

| 项目 | 核验结果 |
| --- | --- |
| 项目 | `/home/hp/workspace/HpAgent_web` |
| HEAD | `be57e0bd22623974ce57498aed78c086fc5f6673` |
| 提交 | `feat(web): implement UI-4 workspace and fix review findings` |
| 提交时间 | 2026-10-07 18:06:09 +08:00 |
| 开始时工作区 | `git status --short` 为空 |
| 方案路径 | `D:/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md`（WSL：`/mnt/d/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md`） |
| 主要依据 | 方案 §6 全部、§7.3、§8、§11–14；D01/D06/D07/D08/D09/D10；T01–T19、M01–M21、FE-B2/FE-B4/FE-B6 |
| 前序证据 | [UI-4 实施报告](ui-4-workspace-permissions-report.md)、[UI-4 自检与修复](ui-4-self-review.md)、[UI-3 实施报告](ui-3-execution-diagnostics-report.md) |
| 本次核验 | 静态阅读前后端实现、现有测试入口和文档；未启动服务、执行应用测试或写入业务数据 |

附件中的执行措辞仅作为设计输入。本次用户请求是制定 UI-5 计划并生成 Markdown，因此仅交付本文及文档索引，不实施 UI、不安装依赖、不提交或发布。后续实施需重新记录 HEAD 和 dirty diff，复核本计划的代码事实。

**交付目标：** 用户能在统一任务中心准确区分“需要我处理 / 进行中 / 等待或已计划 / 已结束”，查看完整已加载范围，完成已有任务控制、修订、预算、投递决策、资料管理、成果阅读与收件箱操作；任务与 Run、Artifact、File 上下文连通，保留现有协议和账户隔离。

| 本阶段交付 | 不在本阶段实现 |
| --- | --- |
| 四桶投影、三类业务类型过滤、TaskRow、TaskInspector、深链、分页和实时恢复 | 新的 Work/Run 状态机、数据库迁移、服务端任务桶查询 |
| 创建/修订、暂停/恢复/停止/推进、明确增额、对话关联、输入和权限 | 每周/Cron 计划、批量危险操作、通用外部 operation 人工对账 |
| Research 报告、已有 HTML 引用、输出文件下载/保存、严格成果验收资格 | UI-6 的完整 Artifact 版本/修改交互和采用手工迭代版替换任务交付 |
| 局部收件箱、当前投递与历史区分、需求修订事件 | 服务器未读计数、完整历史 Requirement diff、无限 Run 历史 |
| 本阶段相关响应式、焦点、无障碍和测试 | UI-7 全站样式/旧页壳清理，UI-8 全链路最终验收 |

## 2. 当前实现与差距

链接以仓库相对路径定位，函数/组件名为检索锚点；下列建议文件尚未存在。

| 编号 | 已核验代码事实 | 实施决策 |
| --- | --- | --- |
| C5-01 | [AppShell](../../web/src/components/shell/AppShell.tsx) 任务区堆叠 WorkCreateForm、WorkPanel、WorkResourcePanel、ResearchOutputs、RunLookup、NotificationInbox；部分组件在隐藏页仍挂载 | 替换成任务 Screen 与按需 Inspector；生命周期由统一 controller 接管，不能仅用 hidden 维持后台轮询 |
| C5-02 | [shell](../../web/src/store/shell.ts) 已解析 bucket/type/work/inspect，包含 task kind；type 当前还允许独立 artifact_build | 真正接通筛选；artifact_build 归 general，旧 `type=artifact_build` 规范化为 general，不增加第四种业务类型 |
| C5-03 | [InspectorHost](../../web/src/components/shell/InspectorHost.tsx) 的 task 分支只显示目标与原始 status | 替换为四页签 TaskInspector，继续使用同一个 Host/Surface/返回栈 |
| C5-04 | [WorkPanel](../../web/src/components/WorkPanel.tsx) 逐条展示原始状态，无四桶；只选前两个活动 Work 订阅，固定 10 秒 load | 提取纯投影；订阅选中任务优先；隐藏暂停列表刷新，恢复立即同步 |
| C5-05 | [works store](../../web/src/store/works.ts) load 仅取首页并替换 items；refresh 仅 map 已有行；有 generation 与 row_version 合并 | 增加串行分页、完整性状态和按 ID upsert；深链不依赖首页存在，防止刷新抹去后续页 |
| C5-06 | [HpWork](../../web/src/api/types.ts) 缺少完整 Requirement、时间、schedule、control_epoch、operation_ref、notification_id 及部分 Artifact 来源字段 | 从真实返回补 DTO；不将前端漏声明误报为后端缺接口 |
| C5-07 | works.control/resolveDelivery/increaseBudget 每次调用创建新 key，busy 为单一 Work ID；增额直接翻倍全部维度 | 按命令意图保留 key/payload/版本；每 Work 操作锁；明确输入提高哪些限额，成功才清草稿 |
| C5-08 | [WorkManagement](../../web/src/components/WorkManagement.tsx) 已有创建与修订，但重建 spec 和 timing，写死 Asia/Shanghai；编辑不按任务终态排除 | 完整读取并保留未编辑字段，显式时区转换；按资格开放修订，保留冲突草稿 |
| C5-09 | WorkPanel 的 advance/accept-result 条件较宽；投递最多显示三项，重复风险重试缺独立确认 | 分类与动作分离，增加证据校验和具体投递确认面板，不隐藏其余待决项 |
| C5-10 | [ResearchOutputs](../../web/src/components/ResearchOutputs.tsx) 另查首页 works；runs 为局部共享列表；报告与文件 Promise.all 绑定失败 | 复用 Works 查询；按 work/run 缓存和隔离；报告与文件独立失败恢复，报告按 Markdown 阅读 |
| C5-11 | [NotificationInbox](../../web/src/components/shell/LegacyUtilities.tsx) 首批加载后以 pre/JSON 展示，无分页和回链 | Header 局部抽屉，展示用户内容、时间与可用对象链接，按已有 before 翻页 |
| C5-12 | UI-4 已有 [GrantEditor](../../web/src/components/workspace/GrantEditor.tsx)、[ResourcePicker](../../web/src/components/workspace/ResourcePicker.tsx)、[SaveToWorkspaceDialog](../../web/src/components/workspace/SaveToWorkspaceDialog.tsx)；WorkInputs 仍在 WorkManagement 内 | 复用 UI-4 授权/撤权/恢复机制，提取 WorkInputs 并补 ready/input 前置判断，不再建第二套资源状态 |
| C5-13 | [Work 查询](../../src/work_domain/commands.py) 每页 50、next_before；events after 每批 100；runs 最近 100；[integration.projections](../../src/work_domain/integration.py) 的 artifacts/deliveries/workspace_saves 各最多 100 | 接全任务分页；明示历史和明细上限，不承诺全账户所有历史异常均被聚合 |
| C5-14 | integration 的 resolve_delivery 校验 target 授权、fact 或 fulfillment revision/epoch/receipt；accept_result 校验成功 Run、当前 revision/epoch 和 evidence | UI 只能保守缩小候选，最终以服务端事务裁决；不得简化为当前 revision 即可重发/接受 |
| C5-15 | [sessionLifecycle](../../web/src/store/sessionLifecycle.ts) 已统一 reset/dispose；[workFeed](../../web/src/sse/workFeed.ts) 已有 cursor 去重与重连 | 保留既有隔离和 SSE 协议；新增 controller、草稿、命令和报告缓存必须纳入同一 reset |

## 3. 目标结构与所有权

建议新增以下结构；小组件允许合并，查询/投影/命令的责任不可混合。

```text
web/src/components/tasks/
  TaskScreen.tsx             # Header、类型过滤、任务列表及空态
  TaskSidebar.tsx            # 四桶与计数/加载范围
  TaskRow.tsx                # 单主动作、行动提示、更多菜单
  TaskInspector.tsx          # 概览/成果与执行/使用资料/高级详情
  TaskEditor.tsx             # 新建与完整 Requirement 修订
  TaskBudgetDialog.tsx       # 明确限额调整
  TaskDeliveryDecision.tsx   # 某一投递的事实与确认
  TaskOutputs.tsx            # HTML 引用、研究报告、输出文件、保存状态
  TaskResources.tsx          # Work grants + 既有 inputs
  TaskInbox.tsx              # 通知分页、阅读与回链
  taskPresentation.ts        # T01–T19 分类、原因、排序、类型
  taskActions.ts             # 动作资格/原因/证据需求
  taskOperations.ts          # 意图、key、版本、操作 owner 和错误恢复
  useTaskController.ts       # 列表扫描、可见性、订阅调度
web/src/store/works.ts        # 扩展既有 Work 权威缓存与查询状态
web/src/api/types.ts          # 补齐已有 DTO
web/src/api/resources.ts      # 封装已有 Work/通知查询与命令
```

| 状态 | 所有者 | 规则 |
| --- | --- | --- |
| Work snapshot、列表分页、详情缓存 | useWorks | account generation + work_id；以 row_version 单调合并，不复制到 Shell |
| runs、历史 events、报告、输出 | Works 查询层或其独立模块 | account + work_id/run_id；按需请求，有界缓存，报告不存 URL/localStorage |
| bucket/type/workId、Inspector/返回栈 | useShell | 筛选 replace；主动打开对象 push；关闭返回原筛选和滚动位置 |
| 桶/标签/计数/排序 | taskPresentation 派生 | 无独立可写状态；列表、Sidebar、Inspector 使用同一个结果 |
| 编辑草稿、确认对象、操作反馈 | 对象绑定 UI/operation state | account + work_id + operationId；退出清空；网络错误保留草稿 |
| Work grants、文件树、保存恢复 | 已有 Workspace controller/operations | 复用 UI-4，不复制权限真相；成功后刷新受影响 Work |
| Run 实时详情与诊断 | 已有 RunInspector/trace store | Work Run 不进入 Chat activeRun，Task 不创建第二套 Run feed |

所有异步成功、catch、finally 都校验账户世代和请求/操作 owner。A→B→A 时第一轮 A 不能覆盖新 A；取消视图读取不等于取消后台任务。403/404 显示统一“对象不可用”，清除敏感详情与动作；网络错误保留同对象最近快照并标记待同步。

## 4. 四桶、类型与排序

### 4.1 投影契约

输入为同一 Work snapshot，另接显式 `now` 用于时间文案和纯函数测试；Runs 缓存只能补“最近执行”和证据，不得以 Run succeeded/failed 决定 Work 终态。

输出至少包含 `bucket、label、reasonLabel、attentionReasons、primaryAction、secondaryActions、sortKey、evidenceRefs、dataWarnings`。动作输出引用 taskActions 的资格结果，不从桶反推动作权限。统计对象先按类型过滤，再按唯一 bucket 计数；切类型时四桶数字同步，Header 标明筛选范围。

以下表从上往下首次命中决定主分类。当前投递仅指 `requirement_revision === current_requirement_revision`；旧 revision 只作历史，但未解除 operation_ref 不能因来源较旧而忽略。

| 规则 | 条件 | 桶 / 标签 |
| --- | --- | --- |
| T01 | completed | 已结束 / 已完成 |
| T02 | stopped | 已结束 / 已停止 |
| T03 | pausing | 进行中 / 正在暂停；另算待决动作 |
| T04 | stopping | 进行中 / 正在停止；另算待决动作 |
| T05 | 当前 delivery uncertain | 需要我处理 / 发送结果待确认 |
| T06 | side_effect_uncertain 或未解除 operation_ref | 需要我处理 / 外部操作结果待确认 |
| T07 | 当前 delivery failed | 需要我处理 / 发送失败 |
| T08 | user_acceptance_required | 需要我处理 / 有成果等待确认 |
| T09 | budget_exhausted | 需要我处理 / 额度已用尽 |
| T10 | awaiting_input | 需要我处理 / 等待你的补充 |
| T11 | blocked，排除合法系统等待及已解决控制状态 | 需要我处理 / 执行受阻，需要处理 |
| T12 | 有 active_coordinator_run_id | 进行中 / 正在执行 |
| T13 | paused | 等待或已计划 / 已暂停 |
| T14 | at_time | 等待或已计划 / 已计划 |
| T15 | retry_after | 等待或已计划 / 等待自动重试 |
| T16 | awaiting_delivery，未命中 failed/uncertain | 等待或已计划 / 等待发送或渠道回执 |
| T17 | waiting_capacity | 等待或已计划 / 等待执行资源 |
| T18 | ready 且无 coordinator | 等待或已计划 / 准备继续 |
| T19 | 其余合法非终态 | 等待或已计划 / 等待继续 |

前置校验和例外必须一起实现：

- 未知 status、缺失 continuation、at_time/retry_after 的 due_at 缺失或非法：非终态进入“状态待核实”，仅查看/刷新；明确 completed/stopped 保留终态并附异常提示。合法未知 kind 走 T19，并记录兼容性诊断，不泄露业务内容。
- 系统等待仅认 waiting_capacity、合法时间等待、正常 awaiting_delivery 等契约组合；不能按 reason 包含 waiting 做字符串猜测。
- `paused + blocked/delivery_resolved` 且无 operation_ref、当前 failed/uncertain、coordinator，排除 T11 后走 T13；`none/control_converged` 同样按实际 paused/stopped 处理。
- pausing/stopping 即使存在投递待决仍只计入进行中；行内提供具体决策，Sidebar 显示该桶的次要待决提示，不能只写“请等待”。
- 一份快照的 continuation.reason 只能有一个值。多原因测试必须由不同真实字段提供事实，例如 uncertain delivery、budget used/limits、当前验收候选和 acceptance criteria；不要为测试构造互相覆盖的多个 reason。
- due_at 过期仍在等待桶，文案为“计划时间已到，等待执行”；不由客户端时钟推进领域状态。

### 4.2 类型与排序

Header 选项为“全部 / 提醒 / 研究 / 通用任务”。reminder→reminder，research_report→research，generic_work/artifact_build/未知 capability→general。artifact_build 标“HTML 生成/修改”，不能由普通新建/修订表单编辑；未知类型可读与安全控制，禁止未知 Requirement 编辑。兼容旧 `type=artifact_build` 时 replace 为 general，并通过 task 深链保留对象选择。

| 桶 | 排序键（依次） |
| --- | --- |
| 需要我处理 | 外部不确定/uncertain → 成果验收 → 预算 → 输入/failed/阻塞；updated_at 降序；work_id 稳定兜底 |
| 进行中 | pausing/stopping 且有决策 → 普通 pausing/stopping → coordinator；updated_at 降序；work_id |
| 等待或已计划 | at_time 的 due_at 升序 → retry_after 的 due_at 升序 → paused → 其余；稳定 ID 兜底 |
| 已结束 | 对应 completed_at/stopped_at 降序，缺失退回 updated_at，最后 work_id |

非法/缺失时间使用明确空值排序，禁止 Invalid Date 导致不稳定顺序。键盘焦点所在行或 Inspector 选中任务变桶时保持上下文，提示新归属；下一次明确导航再应用新位置，避免点击前一帧换行。选中对象可固定显示在列表外“当前查看”，不重复计数。

## 5. 查询、分页与生命周期

### 5.1 DTO 与接口清单

接口全部为现有能力，实施以 [app 路由](../../src/web_api/app.py)、[commands](../../src/work_domain/commands.py)、[integration](../../src/work_domain/integration.py) 和 [Requirement](../../src/work_domain/models.py) 为准。

| 接口 | 使用方式与限制 |
| --- | --- |
| GET `/api/v1/works?before=…` | 每页 50，读取 next_before；无服务端 bucket/type/count 参数 |
| GET `/api/v1/works/{id}` | 返回完整 Work，用于深链、编辑基线和命令后恢复 |
| GET `/api/v1/works/{id}/runs` | 最近最多 100，不能伪造下一页 cursor |
| GET `/api/v1/works/{id}/events?after=…` | 按 event_seq 每批 100；满页继续，少于 100 停止 |
| GET `/api/v1/works/{id}/events/stream?after=…` | 复用 subscribeWork，不改 event 协议 |
| GET `/api/v1/runs/{runId}/research/report` | 读取既有 Markdown 报告，不新增 Artifact kind |
| Run 已发布文件、Work inputs/resources、notification-targets | 复用现有查询与权限 adapter；按页签懒加载 |
| GET `/api/v1/notifications?before=…` | 最多 100，无 next_before；满页用末条 notification_id 继续，少于 100 结束，按 ID 去重 |
| Work 创建/revisions/control/budget/link/accept-result/delivery resolve | 原有 POST/PUT 路由；If-Match 与 idempotency key 不变 |

DTO 补齐 Work 时间和 control_epoch、完整 requirement、schedule.desired_enabled/next_due_at、continuation.operation_ref/receipt_ref、Delivery.notification_id/attempts 等实际字段、Artifact producing_run_id/file_id 与保存 entry_id/failure_code。nullable 与缺字段语义按真实返回定义；旧测试夹具需补全或显式覆盖异常兼容，不用强制断言绕过类型。

### 5.2 全量任务扫描

1. 单一 controller 管理 `scanId、nextBefore、seenIds、loadState、lastCompletedAt`，状态区分 initial/scanning/complete/partial/error。首页返回立即展示，再串行追页；首屏不等待全部任务下载。
2. 每页按 work_id upsert；较低 row_version 不覆盖高版本。列表查询与深链 GET 使用同一个合并函数；详情读取必须能插入此前没有的任务。
3. 扫描中和失败时显示“已加载 N 项 / 计数加载中或同步未完成”；不把局部 attention=0 说成全账户无待办。扫描完成后可显示本轮已遍历任务的四桶数量及同步时间，不宣称事务级实时全局快照。
4. 扫描或刷新不重叠；10 秒恢复周期从本次完成后再安排，若正在扫描只记录一次刷新需求，不堆积 timer。显式刷新可废弃旧 scan token 后重新从首页开始。
5. 追页、SSE、创建和单对象响应可交错；扫描中不删除未出现在首页的任务。首版采用保守合并，不以列表缺席自动删除；对象明确 403/404 才移除其可读缓存。若以后回收缓存，需要完整扫描证据，并保护扫描开始后 upsert 的对象。
6. 请求失败保留已加载行和 cursor，支持继续；检测重复 cursor/不前进页，停止追页并标 partial，防止无限请求。大规模情况下允许取消扫描，恢复时仍标部分加载；服务端筛选/计数留给 BE-T3。
7. 新建发生在扫描过程中可能位于已扫首页之前；命令返回先 upsert，随后安排首页恢复。对其他客户端新建，在下一轮同步补齐，不将本轮遍历包装为强一致统计。

Work 明细最多 100 条的限制与“Work 列表分页完成”分开表达。达到 100 条时标“仅展示最近明细，可能还有更早记录”；无 truncated 字段时不声称确已截断或完整。不能为补历史而猜测私有接口；全量未决项保证依赖 BE-T3。

### 5.3 订阅与对象读取

- 总 Work SSE 上限为两个，由 controller 独占：当前打开的未终态 Work 优先，再选可见高优先行；paused 但有待决的选中对象也属于未终态。切任务/过滤时先释放旧订阅，避免瞬间叠加多组。
- 任务页可见或其他页面打开 Task Inspector 才启用对应消费者；document hidden 时暂停列表轮询，可释放 Work feed，恢复可见立即取快照并重新接续 cursor。切页不停止后台 Work。
- StrictMode、Inspector 开关和 Sidebar 不得重复启动 controller。现有 useWorks.load 的外部消费者保留兼容入口，避免 UI-4 SubjectPicker 或旧 bridge 另开扫描。
- SSE receive 继续去重、触发有界详情刷新；同 Work 并发 refresh 合并。实时最近事件缓存与“完整修订事件分页 cursor”分开，不能把最新 SSE cursor 当历史第一页起点而漏历史。
- Runs/报告仅选中对象或可见行按需加载，列表最近执行信息无缓存就省略；可见行限并发，例如 3，不给每个任务建立订阅。
- 退出同步递增 generation、abort 查询、清 timer/feed、草稿、命令和报告缓存。异步 finally 不得解锁其他对象的操作。

## 6. 页面与对象交互

### 6.1 Screen 与 TaskRow

Sidebar 仅四桶；Header 放类型、刷新、新建与收件箱。筛选使用既有 URL 枚举，无独立研究页或成果入口。空态区分初次加载、全量完成后无任务、筛选无结果、部分加载和网络错误，错误时仍可查看已加载对象。

TaskRow 展示标题、精确标签、一条优先行动提示、真实下次时间、最近已知执行、可访问成果和模型预算使用比例。默认一个主动作加更多菜单；内部 ID、原始状态不铺在主列表。

下次时间：continuation 为 at_time/retry_after 时用其 due_at，否则只在 schedule.desired_enabled=true 时用 next_due_at；paused/pausing/stopping 主行显示控制状态，旧计划留详情。显示 requirement.timing.timezone，单次时间明确展示时区，不静默按浏览器时区改写。

### 6.2 TaskInspector 与导航

| 页签 | 内容/动作 | 加载与错误边界 |
| --- | --- | --- |
| 概览 | 目标、类型、计划、预算、状态理由及可用动作 | 先加载单对象；禁用理由就地显示 |
| 成果与执行 | HTML、研究报告、文件、投递与保存结果、最近执行；接受指定候选 | 报告/文件/执行查询分别恢复，不能某项失败抹去其余内容 |
| 使用资料 | Work grants、inputs；复用 ResourcePicker/GrantEditor | 默认读取权限；写/创建/删除放高级确认；资源编辑草稿保留 UI-4 导航保护 |
| 高级详情 | 当前 revision/控制信息、revised 事件及原因、通知目标、诊断入口 | 无历史 Requirement API，不显示伪造 diff；通知目标未知类型只读 |

增加合法 Task tab（如 outputs），按 kind 校验；不用 Run/File tab 值误解释 Task。`#/tasks?work={id}` 在任何桶都直接 GET 并打开；对象不存在不默默改选列表首项。Task→Run/Artifact/File 进入现有 Host 内部返回栈；普通兄弟任务替换当前层。关闭返回触发行，行不可见则回列表标题。

Research 报告用现有 react-markdown/remark-gfm，不开启 raw HTML；报告引用和已发布文件使用真实 API。已有 file_id 直接进入 UI-4 SaveToWorkspaceDialog，不下载再上传，不自动授权。自动 workspace_saves 的 succeeded/failed/pending 与手动保存分别表达，失败不展示虚假 node 链接。

HTML 打开传 artifact_id/version_id 与 origin.workId，原任务交付版本单独标识。UI-5 只接通既有 Artifact Inspector；UI-6 继续负责完整版本修改体验。查看/保存/生成新版本均不等于接受成果。

### 6.3 收件箱

任务 Header 图标打开局部抽屉；移动端与 Inspector 互斥呈现，保留原 Inspector 描述以便关闭后恢复，不叠两个可交互 modal。通知显示可信用户内容、时间、渠道已存入收件箱状态，work_id/run_id 存在时提供回链；回链仍须重新鉴权。

通知 content 为未知结构时仅显示安全摘要与“查看详情”，不把整个内部 JSON 当主视图。只在用户打开后查询，支持刷新和按 before 加载更多；不造 read/unread 写入、红点或服务器未读数字。没有通知与加载失败分开显示；accepted 不翻译为“用户已读”。

## 7. 命令、表单与恢复语义

### 7.1 公共命令封装

每次明确意图固定 `{accountGeneration, operationId, workId, action, payload, rowVersion, key}`。同 Work 同时只提交一个会影响版本的 UI 命令；不同 Work 错误/锁互不覆盖。网络响应未知保留原 payload、If-Match 和 key 重试；确认成功后结束意图，下次动作生成新 key。修改参数或冲突后用户重新审阅提交属于新意图，不能把新参数塞进旧 key。

复用 ApiClient 的认证、CSRF 和错误机制。409/版本冲突刷新单对象，但不自动覆盖/重交草稿；403/404 显示不可用；业务拒绝展示原因和 request_id。返回快照先 upsert，若响应不含完整快照则 GET 恢复；命令成功但 GET 失败应显示“操作已提交，状态同步失败”，不能当成命令失败再次提交。

资源命令保留 UI-4 workCommand 的明确语义，接入共享的 Work 快照失效/合并，不为统一目录结构重写其恢复实现。跨标签并发仍以服务端版本裁决。

### 7.2 动作资格

| 动作 | 前端条件 | 提交与结果 |
| --- | --- | --- |
| 暂停 | active | POST pause，允许返回 pausing，不预置 paused |
| 恢复 | paused | POST resume，重新读取计划，不恢复旧 due_at |
| 停止 | active/pausing/paused | 说明停止后不再按原计划继续；允许 stopping，未决事实持续可见 |
| 修订 | active/pausing/paused，reminder/research_report/generic_work | 最新 Requirement 为编辑基线，POST revisions；不修改 artifact_build |
| 推进/重试 | active、无 coordinator/operation_ref、非 awaiting_input/awaiting_delivery、due 不在未来 | 预算耗尽先增额/修订；未知结构禁用；unresolved_effect 最终仍由服务端检查 |
| 提高预算 | 非 stopped/completed 且预算存在 | budget_version + Work If-Match，仅提交提高的现有维度 |
| 接受成果 | active、无 coordinator、当前 revision、合法成功证据、需要 user_acceptance | revision + artifact_version_id；确认明确具体版本，服务端裁决 |
| 对话续接 | 用户显式选择目标 Conversation | PUT link；只关联，不复制权限或自动发消息；已有链接不重复创建 |
| 添加 input | metadata 证实 ready 且 purpose=input | POST inputs；普通 output 走 Workspace grant 或既有引用能力 |
| 撤销授权/输入 | 有可管理的对应引用 | 复用 UI-4；显示下一轮生效或受影响执行停止结果，不能把二者混称 |

### 7.3 创建和完整修订

- 创建仅提醒/研究/通用任务，立即/指定时间/每天；通用 reasoning_mode 沿用 react/plan_and_execute 及真实能力门控。artifact_build 由 Artifact 服务创建，UI 不伪装普通创建能力。
- 表单包含名称（创建时）、目标、约束、计划和明确时区；编辑显示当前 revision、change_reason 和修改会取消/收敛当前 coordinator 的提示。暂停任务修改后不自动恢复。
- 编辑先 GET，冻结 row_version 和 requirement 原值；按字段修改，而非重建整个 spec。保留 spec 未编辑字段、acceptance_criteria、resource_requests、deliverable_policy、completion_mode 和 timing.timezone；用户明确改类型/计划时才按合法契约调整相应字段并展示变化。
- datetime-local 不能直接借浏览器默认时区解读后再标 Asia/Shanghai。初版提供明确支持的输入时区与转换，已有其他合法时区原值必须保留；不能可靠编辑的时区计划只读并解释，不静默改成上海时间。增加非上海浏览器时区回归。
- 网络失败保留全部输入；409 后展示旧基线与新 revision 提示，由用户重新审阅后提交。切任务/关闭表单有未提交草稿时使用既有 Surface/导航保护，不影响已生效权限。
- 新建成功立刻 upsert 并打开返回 work_id；即使不在当前桶也保留选中。页面刷新响应未知时不自动重建任务；本阶段内存意图恢复不承诺跨刷新持久重放。

### 7.4 预算与成果验收

预算口径为 `used.model_total_tokens / limits.model_total_tokens`，文案“模型预算已用”；reserved 独立显示“已预留”。限额为零/缺失不计算百分比，超限显示真实数值；其他维度展开。增额对话框展示旧值、新值、增加量，不能默认翻倍所有维度，不能降低或增加未知维度，不预测剩余执行次数。

成果候选至少要求 role=deliverable、source_requirement_revision 为当前、completed、未被当前 revision 接受，并检查 acceptance_criteria 中的 user_acceptance。若 receipt_ref 指向具体 Artifact version，优先定位该版本；进一步查询真实 Work Run，核对 succeeded、requirement_revision、work_control_epoch 与 result evidence。来源 Run 不在最近 100 条中时使用既有单 Run 查询，不用列表缺席直接判无效；仍不可证明则只允许查看。

Artifact 新 v2 不自动替代原任务交付 v1，输入/evidence 引用也不是可验收 deliverable。UI-5 即需严格限制接受入口，不能把已有危险入口留到 UI-6 才修；UI-6 负责进一步统一版本交互，BE-A2 负责未来正式替换交付的契约。

### 7.5 投递与未决外部操作

按 delivery_id 展示渠道、purpose、当前/历史 revision、receipt level、失败原因和后果；所有已加载待决项可逐一访问，取消旧列表 slice(0,3) 造成的操作遗漏。

| 决策 | 必须说明的后果 |
| --- | --- |
| accepted / 确认已送达 | 确认发送已经发生，记录 explicitly_confirmed；不是接收者已读 |
| not_sent / 确认未发送 | 当前有效投递可能回 pending 并重新尝试发送；历史/无效投递回 cancelled；不能写成只登记事实 |
| retry_accepting_duplicate_risk | 独立确认可能重复发送；只供当前有效候选，禁止默认选中或网络重试自动决定 |

前端区分 fact 与 fulfillment：fact 不机械套用 active/revision 限制；fulfillment 至少检查 active、当前 revision、notification_id 与 continuation.receipt_ref。完整 control_epoch/target 授权并未全量投影在 Delivery DTO 中，服务端仍最终判断；不能承诺一定重发成功。pausing/stopping/paused 的 fulfillment 可核对是否发生，但不能无条件重发；not_sent 后依据返回状态显示实际后果。

终态历史告警保留但不提供重发或重新激活任务。普通 side_effect_uncertain 若没有对应 Delivery，显示 operation_ref、执行详情和核查指引，不提供虚假的“确认已解决”；advance、修订、停止均不是清除未决操作的替代命令。

## 8. 实施顺序与逐步退出标准

各步骤按依赖推进，每步形成可审查 diff 与定向测试；本计划不预先声明工时或验收成功。

| 步骤 | 主要产出/允许修改 | 退出标准 |
| --- | --- | --- |
| UI-5.1 契约和投影 | types/resources、taskPresentation/taskActions、真实夹具 | T01–T19、M01–M21 全通过；类型/排序/异常/时间规则唯一；无新后端 API |
| UI-5.2 查询与生命周期 | works、useTaskController、sessionLifecycle、必要 SubjectPicker 接线 | 超过 50 条分页不漏；深链 upsert；无重叠刷新；两个 feed 上限与账户隔离通过 |
| UI-5.3 任务页面与 Inspector | TaskScreen/Sidebar/Row/Inspector、AppShell、InspectorHost、shell、局部 CSS | 四桶/三类型真正驱动视图；单 Host；选中跨桶不消失；移动端/错误/空态可用 |
| UI-5.4 表单与命令 | TaskEditor/Budget/Operations、WorkManagement 提取 | 完整修订保留字段；固定意图 key；409 草稿不丢；预算与控制资格正确 |
| UI-5.5 成果/投递/资料/收件箱 | Outputs/DeliveryDecision/Resources/Inbox、ResearchOutputs/LegacyUtilities 迁移 | 原动作全部可达；投递后果明确；报告和下载保存可用；接受成果证据不串版本 |
| UI-5.6 回归与交接 | 新增/更新测试、验收记录、入口映射与索引 | 前端四项检查、受影响 E2E/后端契约及截图记录完整；未覆盖环境明确；无未处理 P0/P1 |

UI-5.3～5.5 未全部接通前，不将删掉旧入口的半成品作为最终交付。旧组件可暂时作为 adapter，但同一页面只能有一套列表 controller/订阅；UI-7 再做无关旧壳清理。回退仅回退前端视图/接线，不能回写服务器 status、清空 Work 或撤销用户文件。

## 9. 验证与验收矩阵

### 9.1 冻结 M01–M21

以可复用 fixture 做表驱动测试，同时断言 bucket、label、attentionReasons、动作资格和证据引用；四桶互斥且穷尽合法输入。

| 向量 | 输入 | 必须结果 |
| --- | --- | --- |
| M01 | completed + 历史 uncertain | 已结束/已完成；告警只读，不重发 |
| M02 | stopped + ready | 已结束/已停止 |
| M03 | pausing + 当前 uncertain | 进行中/正在暂停；具体投递确认仍可达 |
| M04 | stopping + operation_ref | 进行中/正在停止；持续显示未决，不保证很快完成 |
| M05 | active + 当前 uncertain + coordinator | 需要我处理/发送结果待确认 |
| M06 | active + 旧 revision uncertain + 当前 ready | 等待/准备继续；旧异常留历史 |
| M07 | active + awaiting_input/user_acceptance_required | 需要我处理/有成果等待确认 |
| M08 | active + blocked/budget_exhausted | 需要我处理/额度已用尽 |
| M09 | active + blocked/attempt_failed | 需要我处理/执行受阻 |
| M10 | active + retry_after/attempt_failed + future due | 等待自动重试，无立即推进主按钮 |
| M11 | active + blocked/未知 reason | 需要我处理，未知原因高级区可见 |
| M12 | active + coordinator + 普通 ready | 进行中/正在执行 |
| M13 | paused + blocked/delivery_resolved + 无待决 | 等待/已暂停 |
| M14 | paused + 当前 uncertain | 需要我处理；不能无条件重发 |
| M15 | active + ready/waiting_capacity | 等待/等待执行资源 |
| M16 | active + at_time + 过期 due | 等待/计划时间已到，不是 running |
| M17 | active + awaiting_delivery + pending/sending | 等待回执，sending 不等于完成 |
| M18 | active + none，无动作/coordinator | 等待继续，none 不等于终态 |
| M19 | ready + Run succeeded + ongoing daily | 按 Work 分类，不判完成 |
| M20 | uncertain + 预算耗尽事实 + 待验收事实 | 一个桶，保留全部待决，操作绑定对应证据 |
| M21 | 非终态未知 status 或缺 continuation | 状态待核实，仅查看/刷新 |

补充非法 due_at、终态损坏历史、未知 kind/capability、旧 operation_ref、artifact_build 归 general、排序同值稳定性、时区与预算零/超限、当前 failed 与验收冲突等边界用例。

### 9.2 功能与竞态

| 编号 | 场景 | 主要测试位置（待新增或扩展） |
| --- | --- | --- |
| V01 | 第 51/101 条才有待办；分页失败/恢复/重复 cursor；首页刷新不删后页；扫描中新建和 SSE 高版本 | store/works.test.ts、tasks controller tests |
| V02 | 刷新深链到未加载任务；404/403；筛选外选中；Task→Run→File 返回；浏览器前进/后退 | shell.test.ts、TaskInspector.test.tsx、App.test.tsx |
| V03 | 同 Work 命令双击；不同 Work 并发；响应丢失同 key；成功后 GET 失败不重发；409 保留草稿 | taskOperations.test.ts、works.test.ts |
| V04 | 修订保留 spec/criteria/policy/resource/timezone；暂停修订不恢复；已结束不可编辑；非上海浏览器时区 | TaskEditor.test.tsx |
| V05 | pausing/stopping 待决不隐藏；fact/fulfillment、历史 revision、失效 target、not_sent 后重发/取消 | TaskDeliveryDecision.test.tsx + 后端 integration |
| V06 | 验收旧 revision/错误 epoch/无证据/较新手工版本拒绝；有效候选接受；研究原报告仍可读 | TaskOutputs tests + 后端 integration |
| V07 | 提高一个预算维度、不能降低/新增；budget_version 冲突；used/reserved 分离 | TaskBudgetDialog.test.tsx + 后端预算契约 |
| V08 | 报告成功文件失败及反向；A→B→A；网络/404；保存不重传、不自动授权 | TaskOutputs、workspace 既有测试 |
| V09 | 账户 A→B 迟到 list/run/report/notification/mutation 不回灌；隐藏/恢复/StrictMode；SSE ≤2 | sessionIsolation.test.ts、controller tests、workFeed.test.ts |
| V10 | Work grants/inputs 撤销、候选分页、输出不能当 input；UI-4 dirty guard 不回归 | WorkspaceCommands/ConversationResources + TaskResources |
| V11 | 收件箱 100+、重复通知去重、失败重试、回链；无“已读”虚假状态 | TaskInbox.test.tsx |
| V12 | 全键盘、焦点回归、modal inert/Esc 层级、长标题、200% 缩放、reduced-motion | TaskScreen tests、a11y.spec.ts、新 ui-5-tasks.spec.ts |

真实后端场景复用 [test_work_foundation.py](../../test/web_api/test_work_foundation.py)、[test_foundation.py](../../test/work_domain/test_foundation.py)、[test_work_integration.py](../../test/web_persistence/test_work_integration.py) 和相关 Workspace 权限契约。不能只用前端 mock 编造 pausing 收敛、验收或投递结果作为唯一证明。

### 9.3 计划执行命令与环境

以下为后续实施验收命令，**本次未执行**。定向 Vitest 文件在新增后运行；完整交付运行：

```bash
cd /home/hp/workspace/HpAgent_web/web
npm run typecheck
npm run lint
npm run build
npm test
npm run test:e2e -- e2e/ui-5-tasks.spec.ts e2e/manual-repair.spec.ts e2e/ui-3-execution.spec.ts e2e/ui-4-workspace.spec.ts e2e/artifact.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts
```

在遵循[测试指南](../development/testing.md)准备可丢弃测试数据库、三个角色 DSN、独立 Redis DB、文件目录和端口后，再运行数据库/API/E2E；相关 fixtures 会清表。Playwright 保持 workers=1，避免同账号会话互踢。后端聚焦命令：

```bash
cd /home/hp/workspace/HpAgent_web
PYTHONPATH=src python3 -m pytest test/web_api/test_work_foundation.py test/work_domain/test_foundation.py test/web_persistence/test_work_integration.py -q
```

状态难以由 Fake Executor 产生时，在隔离测试中使用真实后端 fixture/契约构造，记录是否 mock、是否真实事务。Fake Executor 不证明 Temporal/真实 QQ 行为；真实渠道只使用可控测试目标。长测试按仓库长命令策略等待，不反复短轮询。

截图覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080；至少记录四桶、投递确认、打开/关闭 Inspector、错误/空态、长标题、reduced-motion。正常文字对比度 4.5:1、必要非文字边界 3:1；状态不只靠颜色。流式内容不逐 token 播报。

## 10. 交付记录、兼容与后端边界

### 10.1 旧入口到新入口

| 原入口 | UI-5 入口 |
| --- | --- |
| WorkPanel 的状态/控制/预算/成果/投递 | TaskRow + TaskInspector + 明确确认表单 |
| WorkCreateForm 新建/修订选择器 | Header 新建 + 当前任务编辑，默认绑定已选对象 |
| WorkResourcePanel、WorkInputs | TaskInspector 使用资料，复用 UI-4 |
| ResearchOutputs 独立列表 | 研究类型过滤 + 对应 Task 的成果与执行 |
| RunLookup 与诊断 | 任务执行记录下钻；保留按 Run ID 查询的高级入口 |
| NotificationInbox JSON 区 | Header 收件箱抽屉 |
| 当前对话续接 | 任务更多菜单显式选择目标 Conversation |
| File/Run 来源中的 task 深链 | 同一 TaskInspector，单对象加载，不依赖任务首页 |

在删除旧组件/分支前逐项证明入口可达；TestPages 内 Artifact 和诊断的非本阶段代码不整文件删除。

### 10.2 独立后端增强

| 编号 | 现有边界 | 本阶段处理 |
| --- | --- | --- |
| BE-T1 | 无每周/自定义周期 | 仅 immediate/once/daily |
| BE-T2 | 无完整 Requirement 版本读取，Run 最近 100 | 展示 revised 事件/原因和当前要求；不伪造 diff/无限历史 |
| BE-T3 | 无服务器四桶/计数/审批聚合；明细投影上限 100 | 全任务分页、范围提示、Run 内审批；不承诺全量未决明细完整性 |
| BE-X1 | 无通用 operation 人工 resolve | 持续显示未决、提供核查/诊断，不提供假解决动作 |
| BE-A2 | 无手工迭代 Artifact 替换任务交付的正式契约 | 保留原交付版本，严格接受资格；完整 Artifact 体验归 UI-6 |

这些增强不进入 UI-5 前端依赖链。若实施发现确需改领域/后端才能满足新增要求，单独登记范围与阻断的具体体验，不在 UI 重构内隐式修改协议。

### 10.3 完成定义与证据

建议交付 `docs/implementation/ui-5-task-center-report.md` 与 `artifacts/product-acceptance/ui-5/`，记录实现 HEAD、dirty diff/文件清单、D/T/M/FE-B 映射、全部命令结果、截图、未覆盖环境及遗留 BE 项。至少保存成功、失败恢复、并发/迟到响应三类操作过程。

退出条件同时满足：M01–M21 通过；分页范围诚实且不漏已扫描待办；pausing/stopping 决策可达；原 Work 动作和 Research 输出有新入口；字段保留、版本/幂等/权限/账户隔离不回归；关键 E2E 与真实后端状态契约有证据；无未解决 P0/P1。尚未执行或环境受限的项明确列为未覆盖，不能用文档计划或历史阶段通过记录代替本阶段验收。
