# 证据索引

E 编号是本次源码复核锚点；H/D/U 编号沿用 2.1。代码未发生业务变更不等于 2.1 的每个推断都已复核，本次只对决策相关边界直接阅读、核对。行号与哈希以审计基线为准。测试源码只用于定位验收入口，未在本次执行。

| ID | 复核项 | 代码锚点 | 结论 |
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

E25 的负证据范围是当前仓库 web/src 的路由、API 调用与符号表；不是外部部署、其他仓库或未来需求。E24 结合 Compose 的 build.context 和四份 Dockerfile，并逐字节比较两份依赖。E05 结合 agent/__init__.py 与 brain/actions/durable 的生产 import。E15 结合 standalone validator 与 WorkspaceIsolationRuntime.start。

本次未读取私有部署环境、线上 Temporal History、业务数据库、QQ 历史目录或远端 MCP 工具目录。它们的缺口分别影响 G04–G09，不阻止非迁移的边界决策。
