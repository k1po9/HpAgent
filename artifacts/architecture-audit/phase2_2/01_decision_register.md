# 架构决策登记册 · R2.1

> Historical architecture evidence. Not current architecture documentation.
> 修订 R2.1；所有目标改造 NOT IMPLEMENTED，代码事实与目标分列。

> **Current reconciliation (W5, 2026-09-18):** statuses below preserve the R2.1
> design-time record. The original ACD-17 review bundle is historical and superseded. Final
> ACD-17 scope is Account Access Governance,
> Account/day quota, endpoint tiers, exact immutable provider-request snapshots, governed
> invocation/fallback, uncertainty settlement, compact Trace refs, and Account-owned prompt
> visibility and observability. `AuthorizationPolicy`, human review decisions, durable review
> waits, and approval UI were intentionally removed from the final target, not deferred.

用户指定的 canonical 方向已经明确；具体命名/合同/busy 策略等为审计建议。REVERSED 表示推翻原结论；REVISED 表示依新目标重写；RETAINED_AND_RESCOPED 表示保留方向并调整边界；NEW 表示新增。R2 已重审原 16 项并新增 17/18；R2.1 只修订 01/04/17 及相关合同。本轮不采纳任何“已经完成代码改造”的状态。

## ACD-01 · Web / QQ 共用唯一 Durable Agent 主线

| 项目 | 记录 |
| --- | --- |
| 修订 | REVERSED / P0 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E01;E02;E06;E09;E26;E28;E36;E37;E44;E45；H03；H04；H07；H15 |
| 门禁 | G02;G03;G04;G06（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** QQ 经 QQExecutionHost / Facade / DefaultBrainActionLoop；Web Dispatcher 依开关选择 legacy 或 durable。Durable Activities、loader、trace、workspace 仍带 Web 假设，尚不是渠道中立运行时。Research 已有独立固定 Workflow。

**TARGET DECISION / Target：** Web/QQ 对话触发的 Agent Run 必须统一走 Conversation → PG Message/Session/Run/Outbox → Dispatcher → Durable Runtime → AgentRunWorkflow → ReAct 或 Plan-and-Execute → Durable Activities。Conversation 是 Chat 的 source/context owner；Agent Runtime 保持 surface-neutral、conversation-neutral，不要求所有来源伪造聊天实体。Research 保留独立固定流程。

**Required migration/refactor：** W1 冻结中立输入与 suspend-safe 生命周期：稳定 run_id/account_id/source_kind/source_ref，聊天 context 引用由 Conversation 提供；不将 conversation_id/session_id 或固定 lease_token 作为所有 Agent Run 的永久前置。Run lifetime ≠ Execution lease lifetime；durable wait 释放 Activity slot、workspace lock 和 execution lease，恢复重新 acquire 并向后续模型/工具/释放操作传播新 fencing token。以现有 durable 演进唯一实现，替换 Web loader/profile/event/resource_prep 假设；统一取消、预算、Trace、终态，删除双聊天 loop 与 durable_agent_enabled 分流。不要求立即拆新 package。

**FUTURE OPTION：** Scheduled Task、后台自动化、File/Artifact 触发任务可由自己的 source/context owner 创建共享 Run 并复用 AgentRunWorkflow，无需 Conversation/Message/Session。这里只冻结扩展边界，不新增入口，也不改 Research fixed workflow 或 Artifact Build。

**理由：** 现有差异是收敛起点，不再作为长期产品边界。统一恢复单位和业务权威后，两个入口才能共享审批、追踪与模型审阅。

**取舍：** 需要真正重写 QQ 入站/投递和上下文装配接线，不能只改 Workflow 名；不把 Research、Artifact、Document 包进 UniversalWorkflow。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_durable_agent_contract.py`；`test/test_durable_agent_temporal_integration.py`；`test/test_qq_execution_compat.py`。

**后续回退：** 后续按目标实现提交整体回退并重建开发夹具；不在正式源码留下永久双 loop、兼容开关或备用 registry。

## ACD-02 · 围绕共享运行时与 Surface 装配清理 composition

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P1 |
| 决策依据 | AUDIT_PROPOSAL |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E03;E04;E06;E15；H05；H10；H11 |
| 门禁 | G02;G03（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** worker.py 集中共享依赖、QQ Host、Web legacy/durable、Research、Artifact、后台任务与关闭；bootstrap/qq.py 当前构造 QQ 私有执行栈。

**TARGET DECISION / Target：** 最终形成 shared infrastructure、canonical agent capabilities、surface adapters 三类显式构造职责；进程入口拥有资源启动/关闭。正式整理放在主线、领域统一和 legacy 删除之后，避免先把旧结构搬进新目录。

**Required migration/refactor：** 主线工作只做必要的依赖注入切口；清理阶段取消 build_qq_runtime 的私有 loop 装配，按 shared runtime / QQ delivery / Web delivery 组合。保留一个共享 AccountLockRegistry、pool 与 SandboxManager；Research/Artifact 消费者保持各自事件和 lease 所有权。

**FUTURE OPTION：** bootstrap/runtime.py、bootstrap/surfaces.py 等是落点候选；目录名不先于资源生命周期定案。

**理由：** 目标是只有一份 Agent runtime 的组装；bootstrap/web.py 和 bootstrap/qq.py 不应继续各自产生一套 Agent。

**取舍：** 保留显式构造，无需新 DI 框架；代码解耦不自动授权拆进程。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_web_outbox_recovery.py`；`test/test_workspace_isolation.py`；`test/web_api/test_research_file_composition.py`。

**后续回退：** 以目标构造图为基准逐提交撤回；临时导出仅在同一工作包内使用并在退出时删除。

## ACD-03 · 保全正式协议后删除实验 Agent 依赖

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E05;E14;E19；H01；H04；H15 |
| 门禁 | G03;G05（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** ActionRequest / BrainDecision / ActionResult 位于 agent/protocol.py，被正式 Brain/Action 与 durable 引用；agent/__init__.py 同时加载实验类。

**TARGET DECISION / Target：** 协议属于渠道中立 Agent capability contract。将仍被目标链使用的定义迁到轻量合同模块；迁完正式调用者后删除旧协议路径及 Experimental / Multi-Agent / Blackboard 的非目标实现，不长期重导出。

**Required migration/refactor：** ACD-09 删除前先救出仍被 durable 依赖的 DTO/错误/控制接口；legacy 清完后完成协议归属整理和 import 删除。common/agent_protocol.py 是候选，不要求把所有合同塞入 common。

**FUTURE OPTION：** 若合同数量增长，可评独立 agent_contracts 包；当前不新增合同框架。

**理由：** 不能为了删实验目录连同正式 DTO 一起删；也不需要为未投产的旧 import 保留永久兼容 wrapper。

**取舍：** DTO 字段和工具结果语义要验证，但旧 __module__ 路径不是需长期支持的外部合同。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_action_result_semantics.py`；`test/test_durable_agent_contract.py`。

**后续回退：** Git 恢复改动提交；最终树不保留 deprecated 协议目录。

## ACD-04 · Conversation 核心领域统一到 PostgreSQL

| 项目 | 记录 |
| --- | --- |
| 修订 | REVERSED / P0 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E06;E20;E26;E27;E28;E35;E38；H02；H14；H17 |
| 门禁 | G03;G04;G06（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** Web 已有 PG Conversation/Message/Session/Run/Outbox；QQ 按 account 启动或 signal 长会话 Workflow，短期消息经 SessionStore/Redis/WAL。现有 Run 唯一索引也把 queued 纳入单活跃限制；Research Run 允许 conversation/session 为 NULL。

**TARGET DECISION / Target：** Conversation / Message / Session 属于 Conversation Domain，由 PG 统一权威管理。Run 属于共享 Execution/Lifecycle primitive，由 Conversation、Research Task 或未来其他 source 创建，保留来源与 ownership 引用；共享表不等于 Conversation 拥有全部执行。Admission 不变量是所有 surface 由同一个 PG authority 决定接纳，禁止 QQ 私建 mailbox/内存排队形成第二权威。Redis 仅缓存/在线事件/临时群上下文，Hindsight 仅长期记忆；同账户不自动合并聊天。

**Required migration/refactor：** 统一 QQ 消息 ID、去重、会话绑定、忙时行为、Session 轮换和回复目标；QQ 通过同一事务命令建 Message/Run/Outbox。迁出 QQ WAL/SessionStore 权威职责，保留必要记忆能力。SQLite 若只剩 workspace 元数据可暂留适配器，但不得继续定义产品 Session。Research 共享 Run 基础合同而不强造 Conversation。

**FUTURE OPTION：** 物理包划分后定，本轮只冻结逻辑 owner。首版推荐 single active Run + busy reject；未来可替换为 persistent queue、interrupt、append-to-current-run 等 PG admission policy，无需改变领域边界。

**理由：** 两套 Conversation 权威源增加跨入口历史、取消、恢复与审阅的不一致；在未生产阶段可主动统一，无需为旧 QQ 数据建兼容迁移系统。

**取舍：** 现有 QQ account mailbox 与 Web 单活跃语义不同，首版策略需产品验收。队列/中断/追加策略需要相应 PG 事务、索引与状态规则，但不建立第二 admission authority。等待中的 Run 是否继续占 Conversation admission slot 是策略选择，与 execution lease 释放无关。

**验证入口（未执行；旧合同可按目标替换）：** `test/web_persistence/test_phase_c_sessions.py`；`test/web_api/test_sse_gateway.py`；`test/test_qq_execution_compat.py`。

**后续回退：** 以可重建开发数据库和目标夹具恢复；不设计 QQ 历史回填、长期双写或旧生产状态回滚。任何实际本地文件删除仍需明确资产范围。

## ACD-05 · File 与 Heavy Document 保留独立能力边界

| 项目 | 记录 |
| --- | --- |
| 修订 | RETAINED_AND_RESCOPED / P2 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E07;E08;E16;E36;E39；H08；H14 |
| 门禁 | G03;G10;G13（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** file_domain/persistent.py 混合 SQL 与执行服务；Document Workflow 在 lifecycle，重型 Activity 在独立 worker；Run files 与账户 Git workspace 各有实现。

**TARGET DECISION / Target：** 保留文件规则/事务、格式 provider、Run 文件执行与 Heavy Document 的职责。Persistent Workspace、Run Files、persistent file revisions 三者通过对象引用关联，不能因 UI 统一而变成同一文件系统或业务存储。

**Required migration/refactor：** 先为 ACD-18 提供范围/版本合同，再择机拆 persistent Repository 与运行服务，去掉 domain 对执行层/Workflow 寻址的隐式耦合。保留审批+Outbox、revision CAS、lineage 和跨 PG/对象存储的恢复流程。

**FUTURE OPTION：** 可复用 Query 投影基础设施；不合并 Document 队列或统一所有文件生命周期。

**理由：** 这些边界有不同资源需求与业务语义，统一聊天主线不消除它们。

**取舍：** 保留必要分层，避免 Universal FileService；跨数据库与文件系统不能声称单一事务原子性。

**验证入口（未执行；旧合同可按目标替换）：** `test/web_persistence/test_persistent_web_files.py`；`test/web_persistence/test_file_action_approvals.py`；`test/web_persistence/test_file_lineage.py`；`test/test_document_worker.py`。

**后续回退：** 按能力切口撤回；目标业务幂等与权限测试持续有效，旧 import 无长期支持要求。

## ACD-06 · Research 保持固定有界 Workflow

| 项目 | 记录 |
| --- | --- |
| 修订 | RETAINED_AND_RESCOPED / P0 |
| 决策依据 | USER_DIRECTION |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E09;E10;E11;E35；H07；H10 |
| 门禁 | G03;G04;G10（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** ResearchReportWorkflow 固定阶段、最多三轮迭代；task/Run/Outbox 与调度投影已实现。SQL 明确 Research Run 无 Conversation/Session/trigger message。

**TARGET DECISION / Target：** Research command 经 Run/Outbox/Dispatcher 启动 ResearchReportWorkflow。共享 Run、PG 事务、Outbox 设施、Temporal、Artifact/Budget/Trace 合同，内部流程保持独立，绝不作为 AgentRunWorkflow strategy。

**Required migration/refactor：** 调整共享命令/持久化 import 时保留 Research 的 Run shape、阶段幂等和 PG schedule desired state；不要让 conversation_domain 强迫所有 Run 带聊天字段。

**FUTURE OPTION：** 有资源竞争实证时再评独立 Research queue；Prompt Review 若将来扩展到 Research，须逐模型阶段明确范围，非本轮默认。

**理由：** 固定业务流程的边界来自可验证阶段和产物，不来自渠道。

**取舍：** Dispatcher 可按 run_kind 选择启动入口；选择入口不等于一个包含所有业务的 UniversalWorkflow。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_research_temporal_contract.py`；`test/test_research_schedule.py`；`test/web_persistence/test_research_r0_r2.py`。

**后续回退：** 重建相关开发 History 并回退代码；不为旧线上 History 长期保留阶段版本分支。

## ACD-07 · Artifact 统一结果资产，发布流程保留业务语义

| 项目 | 记录 |
| --- | --- |
| 修订 | RETAINED_AND_RESCOPED / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E10;E11;E12；H09 |
| 门禁 | G06;G10（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** 聊天 Artifact 走专属 Outbox/build；Research 经 SQL function 同时写 Artifact/version/report 引用，Markdown 附件另走 OutputPublisher。

**TARGET DECISION / Target：** Artifact 是独立结果资产 owner，聊天与 Research 共用身份、版本、授权和引用合同。保留 Research 原子发布桥与聊天生成 build；Conversation 仅引用结果，不接管所有产物事务。

**Required migration/refactor：** 适配 QQ delivery 和统一领域后的结果引用/访问授权；将 Research SQL 桥登记为具名跨域事务，保留确定性 ID、版本及所属账户约束。

**FUTURE OPTION：** 若增加新的发布方，再抽窄 publication port；文件本体仍由 File owner 管理。

**理由：** 统一结果合同有利于跨入口访问，强制统一生成过程反而会重复调用模型并破坏原子关联。

**取舍：** 不制造 Universal Asset Store；QQ 链接/附件投递由 surface 适配器负责，不绕过身份授权。

**验证入口（未执行；旧合同可按目标替换）：** `test/web_persistence/test_web_artifacts.py`；`test/web_persistence/test_research_r0_r2.py`；`test/web_api/test_research_file_composition.py`。

**后续回退：** 按发布桥/投递适配切口撤回；保持目标资产合同与事务测试。

## ACD-08 · 配置只表达目标产品的真实能力

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P0 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E01;E13;E14;E15；H01；H03；H12；H16 |
| 门禁 | G03;G05;G11（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** durable 默认 false；single 仍解析 agents.yaml；ChannelsConfig 缺省 console 但工厂未支持；fake 有开发门禁。

**TARGET DECISION / Target：** 目标 Agent 编排无 legacy/durable 选项。完成 canonical 路径后删除 durable_agent_enabled、Multi-Agent/Blackboard 配置、无消费方的迁移开关；保留真实 surface、strategy、review、file 等能力配置。

**Required migration/refactor：** 对齐渠道缺省与现有 napcat 配置，保留显式无渠道模式；不支持渠道明确报错。构建目标期间可暂设 durable=true 便于接线，ACD-09 退出必须删分流与开关。清理 config、环境变量、Compose 和说明的同步引用。

**FUTURE OPTION：** Prompt Review 未来成为 Run 级冻结策略；session_worktree 在完成能力验证前保持明确拒绝。

**理由：** 未投产阶段配置不应成为保留旧架构的后门；仍需独立控制真实能力。

**取舍：** 删除假能力/迁移开关不等于删除所有 feature gate，尤其文件权限和开发模拟器不能混为 legacy。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_model_configuration.py`；`test/test_worker_identity_config.py`；`test/web_api/test_config.py`；`test/test_file_capability_config.py`。

**后续回退：** 回退代码与开发配置集合；最终分发配置不保留切回 legacy 的永久路径。

## ACD-09 · 目标路径验证后主动删除 legacy Agent runtime

| 项目 | 记录 |
| --- | --- |
| 修订 | REVERSED / P0 |
| 决策依据 | USER_DIRECTION |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E01;E02;E05;E06;E22;E23;E40；H01；H03；H04 |
| 门禁 | G03;G04;G05;G06（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** Web 双启动/双注册且 QQ 仍依赖 legacy Host/loop；旧 replay 与注册测试冻结这些合同。相关类型、Activity、常量还被目标能力复用，现阶段不能整目录删除。

**TARGET DECISION / Target：** 以 canonical 双入口路径可工作、旧路径无必要且不可达、目标测试通过、业务权威唯一作为退役门禁。完成 QQ 接入后删除 legacy Workflow/Activity/Host/Facade loop、双分派与兼容注册；Git 和审计承担历史追溯。

**Required migration/refactor：** 按 08 退役清单逐项迁出共享 DTO/常量/记忆和定时能力，再删旧入口与实现。同步替换只断言旧合同的测试，建立目标 durable replay/recovery 基线；删除遗留 fixtures 可随合同退役，不伪称新代码兼容它们。

**FUTURE OPTION：** 未来正式投产后的协议演进重新遵守当时的 History/数据合同；此次未生产前提不是永久关闭 Temporal 确定性要求。

**理由：** 线上 History、旧数据和部署观察期已由用户明确排除，继续等待这些证据只会永久固化过渡结构。

**取舍：** 删除有前置验证，但不需线上盘点或兼容观察窗口；不以删除测试代替证明新路径成立。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_web_temporal_replay.py`；`test/test_durable_agent_contract.py`；`test/test_durable_agent_worker_kill.py`；`test/test_qq_execution_compat.py`。

**后续回退：** 用 Git 撤回整组目标改造并重建开发测试环境；不维持旧部署、双注册或 legacy/ 源码坟场。

## ACD-10 · 删除非目标旧账号、迁移兼容与实验实现

| 项目 | 记录 |
| --- | --- |
| 修订 | REVERSED / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E05;E06;E14;E21;E24;E27;E35；H01；H02；H17 |
| 门禁 | G03;G05;G11（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** PG 身份已有真实生产相关构造链；JSON AccountService 无已证生产构造者，merge-account.py 直接操作 JSON。Multi-Agent 被启动拒绝，但正式协议与实验实现共包。

**TARGET DECISION / Target：** 旧 JSON 账号实现、旧账号运维脚本、Experimental/Multi-Agent/Blackboard 及已无必要的 migration compatibility 属后续主动删除范围。完成目标调用、构建和测试核验后直接移除，不等待旧生产数据迁移。

**Required migration/refactor：** 区分仍创建当前 schema 的 migration SQL/runner 与过时兼容实现：前者保留或经 clean-install 验证后再做 schema baseline，不能按 migration 字样全删。UNKNOWN 类型/协议先核对正式消费者，不能直接判死代码。

**FUTURE OPTION：** schema squash 可单独评审，但不是完成 canonical 主线的前置。

**理由：** Git 足以保留非目标源码历史；活跃 schema 创建与业务协议仍是目标系统必需能力。

**取舍：** 未生产不等于所有本地文件可随便删除；本轮仅登记源码/配置候选，不执行任何数据操作。

**验证入口（未执行；旧合同可按目标替换）：** `test/web_persistence/test_postgres_account_service.py`；`test/test_worker_identity_config.py`；`src/persistence/migrate.py`。

**后续回退：** 源码和配置从 Git 恢复；clean-install 夹具重建，不新建历史账号回填/双写系统。

## ACD-11 · MCP 分离传输、发现投影与执行

| 项目 | 记录 |
| --- | --- |
| 修订 | RETAINED_AND_RESCOPED / P2 |
| 决策依据 | AUDIT_PROPOSAL |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E17;E29;E32；H06；H15 |
| 门禁 | G03;G09;G12（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** mcp.py 同含 HTTP/SSE/stdio session、工具 schema 转换、MCPToolManager；工具目录运行时发现。

**TARGET DECISION / Target：** 按传输/工具投影/生命周期切分，服务唯一 durable capability。工具 schema 与版本必须能被 PrepareModelInput 冻结，InvokeModel 不重新发现/替换工具定义。

**Required migration/refactor：** 在 ACD-17 的快照边界确定后整理 MCP；保留三个真实 transport 的必要语义。工具刷新使下一次快照变化，等待审批中的依赖失效须重审；工具真正执行仍另验风险审批。

**FUTURE OPTION：** 具体文件名后定；无需通用插件框架或新远端协议栈。

**理由：** 拆分收益由职责与 Model Review 合同共同支撑，不再只为缩短文件。

**取舍：** 离线工具列表不穷尽远端行为，验证聚焦所改 transport 的可控协议场景。非目标工具可另经 G05 删除。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_durable_agent_hardening.py`；`test/test_mcp_health_script.py`。

**后续回退：** 按 transport/投影提交撤回，临时导出在工作包结束时删除。

## ACD-12 · 热点整理优先服务输入冻结与稳定查询

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P1 |
| 决策依据 | AUDIT_PROPOSAL |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E18;E19;E29;E30;E31;E41；H06；H15 |
| 门禁 | G02;G03;G12;G13（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** model_decision_activity 同时载入 transcript、加入目标、选工具和调模型；planning/evaluation 有独立模型调用；create_app 混合服务装配与路由。

**TARGET DECISION / Target：** 不按 LOC 批量拆文件；明确将 PrepareModelInput / InvokeModel 边界提升为 ACD-17 的必要工作，将 Workspace Query 路由作为 ACD-18 的查询适配。其他热点按具体变更需求整理。

**Required migration/refactor：** 覆盖 ReAct、plan generation、step decision、replan/evaluate、final/synthesis 所有主 Agent 模型调用，避免仅拆 model_decision 导致规划阶段绕过 review。保留 store/预算/operation 的正确性，允许删除旧 Activity name 与测试合同。

**FUTURE OPTION：** 组件拆文件按职责落点确定；不承诺当前 BrainEngine.snapshot_context 已能当 canonical input。

**理由：** 模型审阅给出了真正的能力切口；现有 input_context 是调用后诊断投影且不含最终 provider payload，不能直接承担审批对象。

**取舍：** 目标完成后不保留旧 model_decision 作为可绕过审阅的第二通路。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_durable_agent_hardening.py`；`test/web_api/test_file_protocol_middleware.py`；`test/test_run_budget.py`。

**后续回退：** 在后续实现分支按能力提交回退；不通过永久双 API/Activity 保持旧调用方式。

## ACD-13 · 依赖单一维护源与可靠全新安装

| 项目 | 记录 |
| --- | --- |
| 修订 | RETAINED_AND_RESCOPED / P2 |
| 决策依据 | AUDIT_PROPOSAL |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E24;E27;E35；H13；H17 |
| 门禁 | G05;G11（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** 根/src requirements 字节一致，但被不同 Docker build context 实际使用；Document 有独立依赖；migration 镜像需要根 SQL 目录。

**TARGET DECISION / Target：** 一份维护源，可保留生成的构建副本；不为消除重复内容破坏 build context。删除 legacy 后重新验证全新镜像和 schema 创建，目标仍支持必要 migration runner。

**Required migration/refactor：** 同步根/src 输入并做差异检查；清理非目标依赖前以实际 import 和目标启动验证支撑；保留 Document 独立依赖。

**FUTURE OPTION：** 若统一 build context 能减少复杂度，可在后续工作包替代同步副本；不在本轮强定镜像布局。

**理由：** 相同文件有真实构建用途；canonical 架构要能从空环境安装，而非仅在旧开发目录运行。

**取舍：** 不顺带升级依赖或删除所有 migration；清单去重排在核心主线之后。

**验证入口（未执行；旧合同可按目标替换）：** `src/Dockerfile`；`src/Dockerfile.web-api`；`src/Dockerfile.migrate`；`src/Dockerfile.document`。

**后续回退：** Git 恢复构建输入并重建开发镜像，无需旧生产部署兼容。

## ACD-14 · 当前架构、ADR 与历史审计各有唯一职责

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P0 |
| 决策依据 | USER_DIRECTION |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E01;E02;E22;E42；H01；H03；H08 |
| 门禁 | G01（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** docs/architecture 的旧单 Agent 说明未完全覆盖 durable；已有历史 closure/audit。当前文档维护约束要求代码事实优先、Excalidraw 人工维护。

**TARGET DECISION / Target：** docs/architecture 只描述 HEAD 已实现架构；docs/adr 保存有长期价值的目标决策与状态。普通过期设计可删除，由 Git 恢复。phase2_1/phase2_2 标明 Historical architecture evidence. Not current architecture documentation.，不能作为当前实现权威。

**Required migration/refactor：** 本轮只给 phase2_2 加分类与修订基线；未来 W0 冻结 ADR 目标并纠正 HEAD 文档，随后每个代码包同步其实际变化。不得在重构前将目标图写为当前图。2.1 标记与正式文档/ADR 整理留到后续实施。

**FUTURE OPTION：** 候选 ADR：Durable Agent Only、Unified Conversation Domain、Research Fixed Workflow、Model Input Review、Workspace Ownership。

**理由：** 保留有决策价值的历史，不保留多份互相矛盾的“当前真相”。

**取舍：** Excalidraw 人工规则仍有效；未来 Markdown 改变后登记 visual drift，不自动改图。

**验证入口（未执行；旧合同可按目标替换）：** `docs/architecture/README.md`；`artifacts/architecture-audit/phase2_1/architecture_drift.csv`。

**后续回退：** Git 可恢复普通过期文档；本次旧决策由上一提交保留，不在目录内再维护一套冲突当前稿。

## ACD-15 · Web 产品查询面与 Research UI 分开排期

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E18;E25;E33;E43；H07 |
| 门禁 | G08;G12;G13（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** Research 后端任务 API 存在，前端无专用任务管理；现有 ApprovalCard 是文件动作审批，未见 ModelInputSnapshot / WorkspaceQueryService 专用合同与路由。

**TARGET DECISION / Target：** Prompt Review 与当前 Conversation Workspace 查询成为明确下一阶段目标，分别消费 ACD-17/18 的稳定合同。Research 专用管理 UI 仍单独定义范围；后端固定 Workflow 不依赖该 UI。

**Required migration/refactor：** 目标 UI 展示同一 canonical snapshot 的权限投影，审批引用其 ID/hash；Workspace 与 Run Files 分区查询。QQ 可通过受认证 review 链接或受验证命令表达同一决定，不另造 QQ 审批状态。

**FUTURE OPTION：** Research 任务 CRUD/调度/报告 UI 和 QQ 详细审阅体验可单独排期；不能成为 canonical 主线收敛的前置。

**理由：** 用户已明确 review/workspace 产品方向，不再把两者留作“无需求”的可选想法；本轮仍不实现任何 UI。

**取舍：** 不把文件审批卡改名就当模型审阅已实现；不承诺 User View 可见所有隐藏输入。

**验证入口（未执行；旧合同可按目标替换）：** `web/src/App.tsx`；`web/src/components/ApprovalCard.tsx`；`src/web_api/app.py`。

**后续回退：** 后续 UI 入口可独立撤回；服务端 canonical input、授权和 workspace 范围不因视图变化而改变。

## ACD-16 · 诚实限定当前拓扑，评估会话级 Workspace

| 项目 | 记录 |
| --- | --- |
| 修订 | REVISED / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E15;E16;E36;E39；H08；H11；H14 |
| 门禁 | G07;G13（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** session_worktree 仅被枚举/validator 接受；现有 workspace_ref=account_repo，按 Session 分支在共享账户 checkout 上切换。只有 single_process_account_lock 有完整进程/账户锁路径。

**TARGET DECISION / Target：** 正式支持模式继续限定已实现共进程共享锁；未实现 session_worktree 应明确拒绝。同时把 Account repository 下按 Session/Conversation 分配 worktree 列为有明确产品动机的演进候选。

**Required migration/refactor：** 先建立稳定 workspace_id/owner/session 绑定与 ACD-18 查询合同，查询不能把当前物理 checkout 冒充任意 Conversation 的工作空间。开放独立 worktree 前验证分支映射、跨进程锁、崩溃恢复和未提交文件归属。

**FUTURE OPTION：** Account repository → 多 Session/Conversation worktree；Session 轮换时是否继承同一 workspace，需单独权衡。本轮不定最终拓扑、不声称已实现。

**理由：** Workspace 可视化给了会话隔离明确需求，但逻辑工作空间先稳定，不要求 UI 一开始就依赖完整 worktree 实现。

**取舍：** 数据库执行 lease 不替代文件系统锁；统一 Agent runtime 也不自动允许多个 Agent worker 争用共享 checkout。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_workspace_isolation.py`；`test/test_workspace_provisioning.py`；`test/test_document_worker.py`。

**后续回退：** 保持已验证共进程模式；未来 worktree 方案以 workspace 绑定和资源完整性验证回退，不仅切枚举值。

## ACD-17 · ModelInputSnapshot 与 Durable Model Review

**Final W5 reconciliation:** the original R2.1 ACD-17 bundle is **HISTORICAL / SUPERSEDED**.
Migrations 037–040 and W5-A–F implement the final snapshot/governed-dispatch, Entitlement,
Account Daily Token Quota, Prompt Visibility, and observability target. The bundled durable
human-review portion was removed from the final target, not deferred.

| 项目 | 记录 |
| --- | --- |
| 修订 | NEW / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E29;E30;E31;E32;E33;E34;E37;E41;E44;E45；H15 |
| 门禁 | G03;G04;G12（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** model_decision_activity 混合准备/调用，BrainEngine 的 input_context 为调用后投影；ResourcePool 会选 fallback endpoint，ModelClient 还转换 messages/tools 并合并参数。文件审批提供 durable wait 模式，但模型快照/审阅未实现；context bootstrap 已可能调用 fast 模型改写检索词。

**TARGET DECISION / Target：** 所有主 Agent 模型阶段统一 PrepareModelInput → 不可变 ModelInputSnapshot → 冻结的 AuthorizationPolicy 校验 → InvokeModel。每个 snapshot 在调用前必须满足对应授权策略，无任何旁路；review_enabled 不永久等于人工逐次审批。首版推荐 off / every_call，前者同样生成策略授权决定。PG 保存 canonical 请求、来源与授权，History 仅 compact refs/等待控制；人工 Approval 绑定 snapshot_id+content_hash，权限视图投影同一对象。

**Required migration/refactor：** W1 已冻结并验证 Run 与 execution lease 分离及 suspend/resume、新 fencing token 传播；W5 仅在此基础接入 Model Review，不再重构基础生命周期。W5 拆 Prepare/Invoke，冻结 endpoint/model/messages/tools/有效参数/provider body 与 AuthorizationPolicy id/version/mode/scope；每个 snapshot 记录对应授权决定，Invoke 校验当前快照、策略、权限和 fencing 后原样发送。人工决定通过 PG 事务与 Outbox 唤醒，保持文件动作审批独立。见 06。

**FUTURE OPTION：** 可扩展 first_call_only / phase_based / policy-based；每个后续 snapshot 仍按冻结策略评估，不能复用第一份 snapshot 的人工批准冒充后续授权。Prompt 编辑创建新快照并重新评估策略，需人工时再审批。辅助检索和 Research 模型审阅仍属未来范围。

**理由：** 用户批准的版本必须等于送至 provider 的应用请求；Trace 或临时打印 prompt 不能提供这个保证。

**取舍：** fallback 或有效输入变化生成新快照、旧批准失效，并重新满足策略；不等于任何模式都强制人工重审。所有 durable wait 必须释放 Activity slot、workspace lock、execution lease，恢复获取新 token。外部模型调用仍有 at-least-once/响应丢失边界，不承诺 provider exactly-once。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_durable_agent_hardening.py`；`test/test_tool_execution_approval_temporal.py`；`test/test_run_budget.py`；`src/resources/model_client.py`。

**后续回退：** 可先用 off 策略验证同一 Prepare/Authorize/Invoke 路径；off 免人工但不免授权校验。UI/人工审批可后续交付，不能允许已批准 A 却执行 B 或另留旧模型旁路。

## ACD-18 · WorkspaceQueryService 与可验证的工作空间视图

| 项目 | 记录 |
| --- | --- |
| 修订 | NEW / P1 |
| 决策依据 | USER_DIRECTION;AUDIT_DESIGN_DETAILS |
| 实施状态 | NOT IMPLEMENTED |
| 源码事实 | E18;E25;E36;E39；H08；H14 |
| 门禁 | G03;G07;G13（见 03 的 R2.1 定义） |

**CURRENT FACT / Current：** 现有 RunResourcePreparation 由 PG owner/session 解析账户 repo 并在锁内切分支；RunFileWorkspace 管输入/临时/输出。没有当前 Conversation 的通用只读 WorkspaceQueryService/API。

**TARGET DECISION / Target：** Workspace owner 提供 get_tree/get_file/get_status/get_diff/get_metadata 稳定只读查询；由服务器通过 Conversation ownership 解析 workspace_id 和视图版本。Persistent Workspace 与 Run Files 两个命名范围保持独立，前端不接触宿主路径/GitRepoManager。

**Required migration/refactor：** 定义 committed 与 working 视图、revision/version token、分页/大小限制/文件类型结果、逻辑路径和权限合同。共享 checkout 下 GET 不得调用会 checkout/provision 的 lease_for_run；非当前 Session 的 dirty 视图明确 unavailable，只按指定 ref 查询已提交内容。细节见 07。

**FUTURE OPTION：** worktree 隔离成熟后可扩展各 Session 的实时工作视图，而不改变前端 query 合同。

**理由：** 工作空间可视化必须回答“是谁的哪个版本”，不能把随时可能切换分支的本地目录列表当稳定产品对象。

**取舍：** 短时同锁读取或返回 version_changed，不跨 API 长期占锁；scratch 默认不向普通视图暴露，查询不具备 shell、checkout、reset 或写文件能力。

**验证入口（未执行；旧合同可按目标替换）：** `test/test_workspace_isolation.py`；`test/test_workspace_provisioning.py`；`test/test_run_file_workspace.py`；`test/test_file_workspace_security.py`。

**后续回退：** 后续查询端点和 UI 可独立回退，不更改文件归属和内容；查询能力不以先开放 session_worktree 为前提。
