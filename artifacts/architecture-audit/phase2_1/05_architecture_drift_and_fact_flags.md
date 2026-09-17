# 文档漂移与事实旗标

| ID | 文档/指导事实 | 当前代码事实 | 类型 | 证据 | 后续事实处理 |
| --- | --- | --- | --- | --- | --- |
| D01 | single_agent/03_component.md: 生产 single-agent loop 只有 DefaultBrainActionLoop.execute 一份 | DurableWebRunWorkflow→AgentRunWorkflow→ReAct/Plan workflows→DurableAgentActivities 绕过 legacy Facade loop | ARCHITECTURE_DRIFT | docs/architecture/single_agent/03_component.md:37;src/orchestration/durable_web_workflow.py:132;src/agent_workflows/agent_run.py:29 | 需要更新设计事实范围 |
| D02 | single_agent/02_container.md 服务清单/拓扑 | Compose 增加 searxng、gotenberg、hpagent-document-worker；f4-e2e-model 另为测试 profile，不能算默认生产业务 | ARCHITECTURE_DRIFT | docs/architecture/single_agent/02_container.md:5;docker-compose.yaml:79;docker-compose.yaml:119;docker-compose.yaml:584 | 需补生产进程/外部依赖边界 |
| D03 | refactor-closure-report.md Legacy 表对整个 src/agent/ 的表述可被误读为全部非生产 | Multi-Agent 编排仍被拒绝，但 protocol.py 是生产协议；包初始化还会加载实验类定义 | ARCHITECTURE_DRIFT | docs/architecture/refactor-closure-report.md:22;src/brain/engine.py:14;src/agent/__init__.py:16 | 应限定为具体实验模块 |
| D04 | single_agent/04_sequence.md / closure report 的 Web 时序只展示 WebRunWorkflow/Host | 仅覆盖 legacy 分支，未覆盖 durable child workflow、approval wait、fencing/data plane | ARCHITECTURE_DRIFT | docs/architecture/single_agent/04_sequence.md:1;docs/architecture/refactor-closure-report.md:42;src/agent_workflows/tool_execution.py:32 | 需区分两条时序 |
| D05 | ChannelsConfig 默认支持 console 的配置暗示 | 工厂缺 ConsoleChannel；YAML 当前用 napcat，缺省对象和 factory 不一致 | ARCHITECTURE_DRIFT | src/orchestration/config.py:86;config/config.yaml:71;src/orchestration/worker.py:946 | 需确定未来配置合同 |
| D06 | web_activities.py docstring: Database-only Web lifecycle Activities | 该模块还持有 execute_agent_activity 并调用注入的 WebExecutionHost | NAME_RESPONSIBILITY_DRIFT | src/orchestration/web_activities.py:1;src/orchestration/web_activities.py:120 | 名称/文档事实范围需评估 |
| D07 | session/store.py docstring: SessionStore 只被 Harness 使用 | 由 bootstrap/qq.py 构造并注入 TurnMemoryService；application/memory.py 使用其接口 | ARCHITECTURE_DRIFT | src/session/store.py:24;src/bootstrap/qq.py:65;src/application/memory.py:1 | 源代码说明已落后于组合 |
| D08 | 指导预调查中的 Document Worker 可能被理解为承载 NormalizeDocumentWorkflow | Workflow 注册在 lifecycle，独立 document worker 只注册 normalize_document Activity | 指导种子修正 | src/orchestration/web_workers.py:173;src/orchestration/document_worker.py:22 | 本轮已按代码修正，无需生产改动 |
| D09 | 指导中的 Message/Research→Artifact Dispatcher 统一示意 | Research 通过 publish_research_artifact SQL function 直接发布报告 Artifact；聊天版本 build 才走 dispatcher | 指导种子修正 | src/research_domain/persistence.py:526;src/web_artifacts/services.py:211 | 本轮已拆开 |
| D10 | 配置 agents.yaml 注释：mode=multi 时生效 | 加载无 mode 条件，但装配拒绝 multi；读取配置不等于启用能力 | NAME_RESPONSIBILITY_DRIFT | config/agents.yaml:5;src/orchestration/config.py:668;src/orchestration/worker.py:803 | 区分加载和启用 |

## 事实确认而非漂移

README 开头和 docs/reference/configuration.md 已明确区分 legacy 与 durable，这两处比 single_agent 组件/时序文档更新；不能把整个 docs 目录笼统标为过时。closure report 关于 PostgreSQL 身份、harness 活跃职责、QQ Facade 主链和 Multi-Agent 被拒绝仍与代码一致；关于 Web 唯一 loop 的历史表述只适用于 legacy。

两份 requirements 字节相同是 DUPLICATE_CONTENT；不同 Docker build context 是使用事实。root persistence 与 src/persistence 是 SQL 资产与 Python 运行层分工，不打重复旗标。file/domain 中的 SQL 与 Research domain/persistence 是 PACKAGE_BOUNDARY_CROSSING，表示物理分包与持久化职责交叉，不直接代表错误。

## Fact flags Top 10（文件/资产行，非符号/包行）

| Flag | 文件数 |
| --- | --- |
| UNRESOLVED_DYNAMIC_EDGE | 47 |
| LARGE_HOTSPOT | 26 |
| HIGH_FAN_IN | 15 |
| PRODUCTION_REJECTED | 14 |
| EXPERIMENTAL_CODE | 13 |
| HIGH_FAN_OUT | 12 |
| ARCHITECTURE_DRIFT | 6 |
| PACKAGE_BOUNDARY_CROSSING | 3 |
| HISTORY_COMPATIBILITY | 3 |
| MIGRATION_GATE | 3 |

阈值：LARGE_HOTSPOT 为文件 LOC≥600（含空行/注释）；HIGH_FAN_IN≥15；HIGH_FAN_OUT≥20，fan-out 含标准库/外部库。热点下钻还包含 roots、registry、factory、workflow/dispatcher/reconciler 和重要 SQL boundary，因此不是“只看大文件”。Flags 仅描述当前事实，没有附带删除/合并/拆分结论。

## H01–H17 逐项求证

| 假设 | 状态 | 结果 | 证据 |
| --- | --- | --- | --- |
| H01 | 部分推翻 | agent/protocol.py 的 ActionRequest/BrainDecision/ActionResult 是生产 DTO。agent/__init__.py 会在导入子模块时加载实验类，但这不等于运行 Multi-Agent；mode=multi 仍被启动代码拒绝。agents.yaml 只要存在就加载，与 mode 判断分离。 | src/brain/engine.py:14;src/actions/runtime.py:16;src/agent_activities/runtime.py:18;src/agent/__init__.py:16;src/orchestration/config.py:668;src/orchestration/worker.py:803 |
| H02 | 实现与数据分别确认 | QQ composition 实例化 PostgresAccountService；account/__init__ 也导出 PG 实现。旧 JSON AccountService 没有当前生产构造链。merge-account.py 直接读写 accounts.json，并未调用 AccountService；不能凭存在该脚本宣称旧类是运维可达。 | src/bootstrap/qq.py:60;src/account/__init__.py:4;scripts/merge-account.py:26;scripts/merge-account.py:48 |
| H03 | 确认双启动路径与双注册 | dispatcher 按 durable 开关选择 NEW start；Worker 始终注册 WebRunWorkflow/DurableWebRunWorkflow。AlreadyStarted 接受两个类型，避免翻转开关后丢失已存在执行。当前 YAML/Compose fallback 为 false，因此 legacy 仍接收默认新请求，不是纯历史遗留。 | src/orchestration/web_dispatcher.py:197;src/orchestration/web_dispatcher.py:220;src/orchestration/web_workers.py:163;config/config.yaml:11 |
| H04 | 确认仍为生产路径 | harness/activities 是 QQ 和反思/指标 Activity；context_builder/prompts 被 Worker/QQ/Web context 组合使用。不存在当前 harness/runner.py，不能把保留目录整体判 legacy。 | src/harness/activities.py:41;src/bootstrap/qq.py:1;src/orchestration/worker.py:774 |
| H05 | 确认组合热点 | worker.py 同时拥有 shared infra、QQ composition/lifecycle、条件 Web composition、channel factory、scheduler、cleanup、reflect/metrics/Research schedules 与 shutdown。文件大只是职责调查触发器。 | src/orchestration/worker.py:103;src/orchestration/worker.py:598;src/orchestration/worker.py:853;src/orchestration/worker.py:1103 |
| H06 | 重新统计并扩展 | 当前最大后端文件为 sandbox/tools/adapters/mcp.py（1545 行），超过预调查中首批热点。热点采用 LOC≥600 或高连接/注册等下钻，SQL/Repository 等不足600但重要的文件仍纳入符号事实。 | src/sandbox/tools/adapters/mcp.py:1;architecture_truth_table.csv;hotspots.csv |
| H07 | 确认固定 Workflow | ResearchReportWorkflow 采用固定阶段表、最多三次迭代，不把 Research 当 Agent strategy。ResearchTaskCommandService 建 Run/Outbox；ScheduleManager 将 PG 配置投影到 Temporal Schedule。domain/persistence.py 实际含 SQL，记录边界交叉。 | src/orchestration/research_workflow.py:106;src/research_domain/services.py:184;src/orchestration/research_schedule.py:56;src/research_domain/persistence.py:526 |
| H08 | 确认多层分工，修正 Worker 边界 | 普通读写、logical file、adapter、Run scope、tenant对象、persistent revision/approval 各有不同生命周期。NormalizeDocumentWorkflow 在 Web lifecycle queue；normalize_document_activity 在独立 hpagent-document 队列，Document Worker 只注册 Activity。 | src/file_runtime/routing.py:48;src/orchestration/web_workers.py:173;src/orchestration/document_workflow.py:18;src/orchestration/document_worker.py:22 |
| H09 | 两条发布路径 | 聊天消息→ArtifactService→artifact_outbox→ArtifactBuildWorkflow→generator/build→version；Research→publish_research_artifact_activity→repository→publish_research_artifact SQL function，另经 ResearchMarkdownPublisher 输出附件。不可把所有 Artifact 都画成先走 Artifact Dispatcher。 | src/web_artifacts/services.py:211;src/orchestration/artifact_dispatcher.py:29;src/orchestration/artifact_activities.py:19;src/research_domain/persistence.py:526;src/file_runtime/research_output.py:1 |
| H10 | 确认真实 handler | user_reminder handler 在 start_worker 注册；scheduler.enabled 控制 load/inject/poll。提醒工具在 Sandbox 注册时不依赖 native_tools_enabled，轮询执行仍受 scheduler gate。 | src/orchestration/worker.py:891;src/orchestration/worker.py:895;src/sandbox/sandbox_manager.py:150 |
| H11 | 确认条件入口 | Standalone main 确实存在但 Compose 没有对应 service；single_process_account_lock 会拒绝，session_worktree 被枚举/validator 接受，而 start() 不获取单进程锁。不能将枚举可选项等同完整 session worktree 隔离实现。 | src/orchestration/web_worker.py:93;src/orchestration/web_workers.py:75;src/workspace/isolation.py:129;src/workspace/isolation.py:171 |
| H12 | 确认开发门禁 | fake executor 默认 false，Compose API 强制 false，production WebApiSettings 拒绝 true。它被 create_app 导入并不使 fake 业务执行成为生产路径。 | src/web_api/config.py:29;src/web_api/config.py:55;src/web_api/app.py:339;docker-compose.yaml:673 |
| H13 | 确认完全相同 | cmp 核对 root/src 两份 requirements.txt 字节相同。主 Agent 的 build context=./src，因此 COPY 用 src 版本；API/migrate build context=. 用根版本。Document 使用独立 requirements-document.txt。 | src/Dockerfile:35;src/Dockerfile.web-api:4;src/Dockerfile.migrate:11;src/Dockerfile.document:15;compose_services.csv |
| H14 | 确认多状态边界 | QQ SessionStore=Redis/WAL/归档；WorkspaceDB=SQLite 本地元数据；GitRepoManager=repo/branch；WorkspaceIsolationRuntime=进程/账户锁；RunFileWorkspace=Run文件范围；TenantFileStore=持久对象；SandboxManager=工具实例生命周期。 | storage_ownership.csv;src/session/db.py:43;src/workspace/isolation.py:151;src/sandbox/sandbox_manager.py:128 |
| H15 | 确认 Model / Tool 分离 | BrainEngine 负责模型决策，经 ResourcePool/ModelClient；ActionRuntime 进入 Sandbox.select_tools/execute，经路由/registry 调用本地/MCP/Skill。MCP 自身1545行，包含连接、适配、工具投影和容错，不能漏于目录粗粒度调查。 | src/brain/engine.py:79;src/actions/runtime.py:101;src/actions/runtime.py:148;src/sandbox/sandbox.py:61;src/sandbox/tools/adapters/mcp.py:1 |
| H16 | 确认配置/工厂错位 | ChannelsConfig 缺省 [console]，仓库 YAML 明确 [napcat]，当前工厂只支持 NapCat/OfficialQQ。console 会被 skip，不能因为枚举或类存在就认为生产支持它。QQ profile 启动 napcat 容器不自动决定 Python channel 列表。 | src/orchestration/config.py:86;config/config.yaml:71;src/orchestration/worker.py:946;src/orchestration/worker.py:961;docker-compose.yaml:852 |
| H17 | 确认合理资产分层 | 根 persistence 是 SQL DDL/migration/init；src/persistence 是 Python Repository/UoW/runner。相同目录名不构成重复；runner 的 migration_dir 与 Docker build context 关联已核对。 | src/Dockerfile.migrate:14;src/persistence/migrate.py:1;schema_objects.csv |
