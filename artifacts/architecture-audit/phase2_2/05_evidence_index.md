# 代码事实证据索引 · R2.1

> Historical architecture evidence. Not current architecture documentation.
> 修订 R2.1；所有目标改造 NOT IMPLEMENTED，代码事实与目标分列。

E01–E25 继承原锚点且哈希核对；E26–E43 继承 R2；R2.1 定向补充 E44/E45，未重审全部 ACD。这里证明当前实现/差距，不能证明目标已实现。未生产前提来自用户 U-PREPROD，不来自 E 表。测试源码均只读未执行。

| ID | 复核项 | 源码位置 | CURRENT FACT |
| --- | --- | --- | --- |
| E01 | 双新启动路径与翻转后的重复启动恢复 | `src/orchestration/web_dispatcher.py:103` | 按 durable_agent_enabled 选 Workflow；AlreadyStarted 接受 legacy/durable 两种类型。 |
| E02 | 双 Workflow 注册及 Document queue 边界 | `src/orchestration/web_workers.py:152` | lifecycle 和 agent 两组定义始终列入注册；NormalizeDocumentWorkflow 在 lifecycle。 |
| E03 | 组合入口职责集中 | `src/orchestration/worker.py:103` | Web Host、durable Activities、Research、Artifact 与消费者由同一组合函数装配。 |
| E04 | 后台任务与资源关闭 | `src/orchestration/worker.py:1103` | 关闭渠道、各后台任务及依赖资源；抽取需保留生命周期和共享实例。 |
| E05 | 生产协议与实验模块共包 | `src/agent/protocol.py:10` | Brain/Action DTO 为生产依赖；agent/__init__.py 顶层加载实验类。 |
| E06 | QQ 身份与短期状态组合 | `src/bootstrap/qq.py:44` | 构造 PostgresAccountService、SessionStore、Brain/Action、Facade。 |
| E07 | 持久文件混合职责 | `src/file_domain/persistent.py:50` | 同文件含 SQL Repository 与依赖 OutputPublisher 的 PersistentWebFileService。 |
| E08 | 审批事务及信号寻址 | `src/file_domain/approvals.py:61` | 审批服务写 Outbox 并使用 ToolExecutionWorkflow ID 算法，非纯值对象层。 |
| E09 | 固定 Research 控制流 | `src/orchestration/research_workflow.py:106` | 固定阶段表，gap 驱动提前结束，迭代至多三轮。 |
| E10 | Research 发布桥接 | `src/research_domain/persistence.py:526` | 确定性 UUID，通过 SQL function 发布；ResearchMarkdownPublisher 另输出附件。 |
| E11 | Research 发布原子操作 | `persistence/migrations/022_research_r3_r4.sql:112` | 函数同时写 artifacts、artifact_versions、research_reports；授权 hpagent_worker。 |
| E12 | 聊天 Artifact Outbox | `src/web_artifacts/services.py:207` | 聊天 Artifact 写 start_artifact_build 事件后由专属构建链处理。 |
| E13 | 渠道配置缺省 | `src/orchestration/config.py:79` | 缺省 console；worker 的工厂仅 NapCat/OfficialQQ，仓库 YAML 指定 napcat。 |
| E14 | 实验配置仍会加载 | `src/orchestration/config.py:668` | agents.yaml 存在就解析；worker init_dependencies 仍拒绝非 single。 |
| E15 | 当前 workspace 隔离能力 | `src/workspace/isolation.py:129` | single_process_account_lock 有进程/共享账户锁约束；session_worktree 仅枚举通过不证明完整实现。 |
| E16 | 独立 Document Activity | `src/orchestration/document_worker.py:20` | 只注册 normalize_document Activity，并发 1；独立 tenant reader 与 document scratch。 |
| E17 | MCP 多种传输 | `src/sandbox/tools/adapters/mcp.py:69` | HTTP、SSE、stdio 三类 session，工具投影与 manager 共处一文件。 |
| E18 | API 组合与路由热点 | `src/web_api/app.py:252` | lifespan 连接/服务构造与 HTTP handler 在一个 create_app 内。 |
| E19 | Durable Activity 职责 | `src/agent_activities/runtime.py:64` | 注入 store/brain/actions/lifecycle/budget/approval/persistent；持有多个 Activity 实现。 |
| E20 | SSE 权威快照 | `src/web_api/sse.py:60` | 从已提交 PostgreSQL Run/Message 查询快照；Redis 承担在线事件传输。 |
| E21 | 旧 JSON 运维工具 | `scripts/merge-account.py:48` | 脚本直接操作 JSON 资产；不能作为旧 AccountService 类的生产调用证明。 |
| E22 | 冻结 legacy History 的范围 | `test/test_web_temporal_replay.py:66` | 测试只 replay 已冻结 completed happy path；不是全量线上历史或 durable 兼容证明。 |
| E23 | 注册合同断言 | `test/test_durable_agent_contract.py:77` | 断言 lifecycle/agent 注册定义列表；本次只读未执行。 |
| E24 | 依赖文件的不同 build context | `src/Dockerfile:35` | main context=src；API/migrate context=根目录；两份 requirements 当前相同。 |
| E25 | 前端当前能力范围 | `web/src/App.tsx:1` | 结合 web/src 全量搜索与 2.1 frontend_symbols，未见任务管理路由/API；仅测试附件名出现 research。 |
| E26 | QQ account 级会话入口 | `src/application/conversation.py:71` | 当前按 account start/signal OrchestrationWorkflow，生成字符串 session_id，并准备本地资源。 |
| E27 | PG 交互实体与单活跃约束 | `persistence/migrations/001_phase_a_schema.sql:51` | Conversation/Message/Session/Run 及 queued/running/cancelling 单活跃索引已存在，未含目标 QQ 适配。 |
| E28 | Web 交互事务与 busy | `src/web_domain/services.py:130` | 先锁 Conversation、查幂等/busy，再建 Session/Message/Run/budget/Outbox；不是已实现中立 QQ 命令。 |
| E29 | 准备与模型调用耦合 | `src/agent_activities/runtime.py:413` | 同 Activity 读 transcript、加 objective、选工具并调模型；没有预调用审阅。 |
| E30 | 调用后输入投影 | `src/brain/engine.py:170` | generate_chat_decision 在模型返回后生成 input_context；不是完整 provider 请求快照。 |
| E31 | 真正 provider payload 转换 | `src/resources/model_client.py:191` | 选择实际 model，转换 messages/tools、填有效 max_tokens/stream 并合并 extra_body。 |
| E32 | 模型回退发生于运行时 | `src/resources/resource_pool.py:108` | selector 解析候选 endpoint，按 attempt 调 client 并做预算 reserve/settle，可能 fallback。 |
| E33 | 文件审批的 durable wait | `src/agent_workflows/tool_execution.py:33` | signal 唤醒后查询权威审批状态，wait_condition 等待；当前是工具/文件审批而非 Model Review。 |
| E34 | 执行 lease 过期与 fencing | `src/agent_activities/store.py:81` | 只有 owner/token/未过期符合条件才续租，不能把长时间审阅后的旧 token 当作自动有效。 |
| E35 | Research Run 非聊天 shape | `persistence/migrations/021_research_r0_r2.sql:31` | Research Run 绑定 Task，conversation/session/trigger/context_seq 为空；同表共享 Run 不等于共享聊天实体。 |
| E36 | 当前 checkout 不是任意会话快照 | `src/workspace/isolation.py:251` | 加载 account_repo/session 后锁账户、准备并切分支，还写 Web session_context；执行准备不能用作只读查询。 |
| E37 | 上下文准备已有辅助模型调用 | `src/agent_activities/runtime.py:242` | loader/记忆组装写 transcript；可先调用 fast 模型改写 recall query；trace surface 当前写 web。 |
| E38 | QQ SessionStore 仍实际存在 | `src/session/store.py:46` | QQ Redis/WAL/checkpoint 现状；目标统一 PG 需要迁移正式消费者，不能以改文档声称已删除。 |
| E39 | Run Files 独立范围 | `src/workspace/file_scope.py:55` | Run 文件输入/临时/输出范围，区别于 Git account/session workspace。 |
| E40 | harness 含混合必要能力 | `src/harness/activities.py:41` | QQ turn 调旧 Host；同文件还有 archive/reflection/metrics，不能按目录整体判可删。 |
| E41 | 规划与评估另有模型调用 | `src/agent_activities/runtime.py:1102` | planning/evaluate_plan 构建专门 instructions 后直接调用 Brain；只拆 model_decision 无法覆盖全部主模型阶段。 |
| E42 | 当前文档维护约束 | `docs/architecture/README.md:5` | 要求实现事实优先、显式 drift 与 Excalidraw 人工维护；目标 ADR/当前文档分工是本次建议。 |
| E43 | 当前审批 UI 范围 | `web/src/components/ApprovalCard.tsx:18` | 调用 listFileApprovals/decideFileApproval 并展示文件目的地；不是模型输入权限投影。 |
| E44 | 当前一次性执行 lease | `src/orchestration/durable_web_workflow.py:86` | 启动 AgentRunWorkflow 前 acquire，固定 token 传入子 Workflow，finally 用同 token release；suspend-safe 分段 lease 是 W1 目标，未实现。 |
| E45 | 当前 Agent 输入硬绑定 | `src/agent_workflows/contracts.py:17` | 必填 conversation_id/session_id 与固定 lease_token；source-neutral 输入及可更新执行 token 是目标，未实现。 |

新功能负证据：对 src 与 web/src 搜索 ModelInputSnapshot、WorkspaceQueryService、prepare_model_input、invoke_model、prompt_review 未命中，再结合模型调用与 API/ApprovalCard 实现复核。这说明本仓库缺少上述目标合同，不声称没有任何相邻能力。Blackboard 未检出独立同名实现，退役按实验类真实消费者处理，不捏造 blackboard.py。

E24 结合 Compose 的实际 build context 与四份 Dockerfile；E22/E23 的旧 replay/双注册测试只描述旧合同，新目标允许在行为验证后替换。代码基线变化后应重新检查适用性；validator 接受仅审计提交导致 HEAD 改变，但拒绝冻结证据或范围外源文件漂移。

未读线上 History、真实业务数据库、私有部署配置；这些不再是 legacy 删除前置。目标 PG/Temporal E2E、MCP transport、workspace 隔离、模型输入一致性仍须在实施时验证。
