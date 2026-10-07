# UI-5 任务中心实施报告

> 2026-10-08 自检补充：确认 3 个待修复 P2（输出缓存不刷新、缺 continuation 的成果页崩溃、重试要求丢失草稿）。本轮已有回归 80 项通过，3 项反例失败；详见 [UI-5 自检记录](ui-5-self-review.md)。下文验证结果保留原实施批次语义，不代表这些新发现已解决。

日期：2026-10-07（Asia/Shanghai）。实施依据为 [UI-5 计划](ui-5-task-center-plan.md) 和附件《HpAgent 前端 UI 重构方案 v1.0》。本次用户请求是实施，计划中“仅交付计划”的段落保留其制定时的历史语义。

基线 HEAD：`be57e0bd22623974ce57498aed78c086fc5f6673`。开始实施时已有 `docs/implementation/README.md` 的计划入口修改，以及未跟踪的 `ui-5-task-center-plan.md`；保留这些内容。2026-10-07 原实施阶段交付为工作区变更，未创建提交；2026-10-08 后续修复和提交范围见 [UI-5 自检与修复记录](ui-5-self-review.md)。源码、测试和证据清单见 [changed-files.txt](../../artifacts/product-acceptance/ui-5/changed-files.txt)，命令与截图入口见 [验收证据](../../artifacts/product-acceptance/ui-5/README.md)。

## 实现结果

任务页原有的创建、Work 列表、资料、Research 输出、Run 查询和通知堆叠，已替换为统一任务中心。Sidebar 按“需要我处理 / 进行中 / 等待或已计划 / 已结束”显示数量，Header 提供全部、提醒、研究、通用任务筛选、刷新、新建和局部收件箱。HTML 生成/修改归通用任务；旧 `type=artifact_build` URL 会规范化。普通列表不以原始状态和 ID 为主要内容，手工 ID 查询保留在折叠高级入口。

Task Inspector 复用已有唯一 Host、Surface、对象返回栈，提供概览、成果与执行、使用资料、高级详情四个页签。Task 深链可以读取首页之外的对象；Task→Run/Artifact/File 子对象保留来源。键盘切换页签、Esc、关闭焦点返回、未保存要求/权限草稿保护沿用 Shell，移动端收件箱打开时隐藏 Task Inspector，避免叠加两个模态面板。

四桶投影和动作资格分别实现为纯函数。completed/stopped 保持终态，pausing/stopping 保持进行中，同时露出投递等次要待决。旧 revision 投递作为历史；未解除 operation_ref 持续显示。过期 due_at 只改变等待文案，客户端不推进状态。未知/损坏快照仅提供查看和刷新。除 continuation 之外的预算、验收事实补充提示，不覆盖 T01–T19 的主分类优先级。焦点行及当前查看对象变桶时保留上下文，直到明确导航再换位。

Work Store 是唯一快照源。串行读取全部 `next_before` 页，首页立即可用；展示已加载范围、扫描中/失败/部分/完成状态，支持暂停和继续。首页刷新保留后续页，详情 GET 可插入缺失任务，row_version 单调合并；重复游标或不前进页终止追页。Controller 只在任务页或 Task Inspector 有消费者且文档可见时运行，完成后再安排 10 秒同步；隐藏/切页释放订阅和扫描，恢复可见即同步。当前非终态对象优先，Work SSE 同时最多两个。

创建/修订支持提醒、研究和通用任务，以及立即、单次、每日计划。明确区分 UTC、Asia/Shanghai 与保留其他已有时区；不依赖浏览器本地时区解释日期。未编辑的 spec、验收条件、资料请求、交付策略、completion_mode 和 timing 保留原值。GET requirement 的记录元数据不回传为领域要求，修订仅发送契约字段。终态与未知能力不开放普通要求编辑；冲突保留输入并要求审阅新基线。

命令按 Work 保存原意图、请求体、幂等 key 和 If-Match；同 Work 串行、不同 Work 独立。响应未知可重试原命令，不能用新参数替换；命令成功但随后同步失败显示“已提交”，不重复执行。账户 reset 拒绝旧响应，关闭编辑器后已提交结果可以更新权威缓存，但不能重新导航或打开面板。资料操作复用 UI-4 controller，并共享 Work 锁及未知结果保留机制。

预算编辑显示 used/reserved、旧限额、新限额和增加量，只提交明确提高的已知维度，不默认翻倍全部额度。投递面板展示全部返回的当前/历史明细，分别解释 sent、not_sent、uncertain；not_sent 明确说明按待发送重试还是取消待发送，重复风险有独立确认。fact 与 fulfillment 使用不同资格；终态不重发。服务端仍负责目标授权、revision、epoch、receipt 及事务裁决。

成果页读取 Work 原交付版本，严格校验 completed deliverable、当前 requirement revision、成功 Work Run、当前 control epoch 与 evidence 后才开放接受。最近 100 个 Run 中缺失来源时按 ID 补查，不把较新手工版本当作原交付。研究报告按 Markdown 阅读，正文与文件列表独立加载/失败恢复；无原始 HTML 注入。文件下载/保存沿用既有 file_id 与 UI-4 保存流程；保存不重传、不自动授权。Work 输入只有 ready/input 文件可以添加；已结束任务不开放新增资料或授权，已有资料仍可检查和撤销。

高级详情提供 revision 事件和原因、完整当前要求、执行编号查询及已有通知目标管理。收件箱按 notification_id 翻页、去重并提供 Work/Run 回链，不伪造“已读”或未读数量。按需只读查询带账户世代、在途去重、最多三路并发和有界缓存；挂载消费者使用的缓存不会被清理。

## 方案与计划映射

| 约束 | 本次交付及证据 |
| --- | --- |
| D01、D07 | 保持 AI/空间/任务三入口；全部对象复用唯一 InspectorHost/Surface；四页签、URL、返回和草稿保护；Shell、App 恢复单测与浏览器深链/焦点用例。 |
| D06 | 四桶、三类业务过滤与 TaskRow；`taskPresentation.test.ts` 冻结 M01–M21 并增加异常、排序、类型和时间边界。 |
| D08 | 保留领域/协议，补全 DTO 和局部生命周期；保留要求字段、创建/修订、预算、投递及验收；`taskRequirement`、`taskOperations`、TaskIntegration 和真实 API/持久化测试。 |
| D09 | 资料和输出复用 UI-4，ready/input 前置检查、授权锁及恢复；完整 Workspace 单测与 UI-4 浏览器回归。 |
| D10 | 只使用已有接口，保留独立 BE 增强事项及范围提示；无服务端契约/迁移改动。 |
| FE-B2 | Work 与需求事件/通知分页、范围提示、详情 upsert；101 条 Store 单测及 51 条真实 API 浏览器用例。 |
| FE-B4 | pausing/stopping 保留进行中主桶和次要决策；M03/M04、TaskIntegration 和浏览器合成投递用例。 |
| FE-B6 | Work timestamps/schedule/control_epoch/operation_ref、Delivery notification_id/attempts、Artifact producing_run_id 等按真实返回补齐 DTO。 |
| 生命周期与恢复 | 账户世代、迟到响应、SSE≤2、可见性恢复、同 Work 锁、原 key 重试；Store、controller、operations、App recovery 与跨账户回归。 |
| T01–T19、M01–M21 | 四桶互斥、领域快照决定主分类；冻结向量全部通过。合成 UI 夹具仅证明界面，后端 contract 验证真实控制/验收/投递规则。 |
| V01–V12 | 分散覆盖 Store、Shell、TaskIntegration、controller、Work/Workspace operations 与 Chromium；详见证据说明，不把未运行的人工/Temporal 场景列为通过。 |

## 验证结果

最终结果见 [验收证据命令表](../../artifacts/product-acceptance/ui-5/README.md)。前端类型检查、lint 和生产构建通过；完整 Vitest 为 42 个文件、316 项通过（101.56 秒）。后端使用三个既有 Work API/domain/persistence 文件，37 项通过（66.39 秒），保留一条 Starlette 弃用提示。生产构建保留大于 500 kB chunk 提示；本阶段未引入依赖或进行全站拆包。

Chromium 八个 spec、28 项全部通过（5.6 分钟），包括 51 条真实任务分页。临时测试父进程在通过报告后收到 SIGTERM，以 143 退出；独立 process session 的服务器清理补验两项通过、exit 0，具体记录在证据页。保留各轮失败与修复原因，不用早期通过次数相加。浏览器运行真实 API、PostgreSQL 和 Redis，执行器为 Fake Executor；网络夹具的四桶、paused/pausing 投递及已有 UI-3 状态图单独标为合成证据。真实模型、QQ、Temporal 未在本批验证。

## 覆盖边界与遗留事项

没有修改后端 Python、数据库迁移、状态机、服务端桶接口或资源契约，没有安装新依赖。既有 UI-2/UI-3/UI-4 截图保留历史版本，本批回归截图另存 UI-5。

- BE-T1：仅 immediate/once/daily；每周及 Cron 未实现。
- BE-T2：事件及修改原因、当前要求和最近 100 个 Run；无完整历史 Requirement diff 或无限 Run 历史。
- BE-T3：完整遍历任务列表，但每个 Work artifacts/deliveries/saves 投影最多 100；范围提示不保证全账户全部未决明细。未新增服务器计数、审批聚合或虚拟化规模承诺。
- BE-X1：operation_ref 保持未决并可诊断，未实现通用外部操作人工 resolve。
- BE-A2：严格使用原交付及证据，不自动以手工迭代 Artifact 代替；完整 Artifact 版本交互继续由 UI-6 处理。
- UI-7/UI-8：全站视觉/旧组件删除及最终全链路验收不在本批。仍保留未挂载的旧组件以控制范围。
- 人工读屏、真实手机软键盘、持续多租户压力、真实模型/渠道及独立 Temporal 控制收敛未验证。后端领域断言不能代替上述环境运行。
