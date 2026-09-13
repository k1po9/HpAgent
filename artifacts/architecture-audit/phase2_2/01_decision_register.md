# 架构决策登记册

所有 ACD 状态均为 **PROPOSED（审计建议，未采纳、未实施）**。DESIGN_READY 表示证据足以确定方向和设计切口；不表示测试、部署或迁移门禁已通过。CONDITIONAL 表示有具体切口但须补行为证据；BLOCKED_ON_EVIDENCE/PRODUCT 仅阻止对应行动。P0 是合同/正确性优先，P1 是明确结构收益，P2 是条件优化；不是生产事故等级。

## ACD-01 · 执行架构收敛到共享能力与显式控制流

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / KEEP_BOUNDARY |
| 优先级 / 就绪度 | P0 / DESIGN_READY |
| 逻辑责任域 | Agent execution（非人员分派） |
| 事实追溯 | H03;H04;H07;H15；E01;E02;E05;E06;E09;E19 |
| 实施门禁 | G02;G03;G04，详见 03 |

**建议：** 保留 QQ/legacy 的 Facade loop、Durable Web 的 Workflow 控制流、Research 固定流程；共享 Brain/Action、工具、模型、预算和事件合同。将 Durable Web 作为 Web 后续恢复/审批能力的优先演进方向，不在本次切换默认值。

**依据及收益：** 三种控制流的状态恢复单位不同，强制包进一个 execute 会遮蔽 Temporal History、审批等待和 QQ 会话合同。代码证据支持共享底层能力，不足以支持 QQ 迁入 durable。

**备选取舍：** 不采纳所有入口统一 Facade：会失去 durable 步骤边界；不采纳所有任务改 Agent strategy：Research 固定阶段并非自由规划。继续无限复制底层能力同样不采纳。

**影响与代价：** 控制流差异保留；新增能力需明确适用入口。允许临时双 Web 路径的维护成本，限制新增第三套通用聊天 loop。

**验证入口（尚未执行）：** `test/test_qq_execution_compat.py`；`test/test_durable_agent_contract.py`；`test/test_research_temporal_contract.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 本次仅定义目标；后续能力抽取保持旧适配器，单项回退，不改变新启动路由。

## ACD-02 · 按构造和生命周期拆分主 Worker 组合热点

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / EXTRACT |
| 优先级 / 就绪度 | P1 / DESIGN_READY |
| 逻辑责任域 | Runtime composition（非人员分派） |
| 事实追溯 | H05;H10;H11；E03;E04;E06;E15 |
| 实施门禁 | G02;G03，详见 03 |

**建议：** 先把 compose_web_workers 及 WebWorkerComposition 迁入拟建 bootstrap/web.py；再把共享依赖构造迁入 bootstrap/infrastructure.py。orchestration/worker.py 保留进程入口、启动/关闭顺序和 channels/schedules 协调。

**依据及收益：** 已有 bootstrap/qq.py 和 build_web_temporal_workers 是可复用边界；1370 行不是唯一理由，构造、资源拥有权和业务适配混合才是切口。

**备选取舍：** 不采纳新增 DI 框架或服务注册中心：当前显式构造足够；不采纳拆容器：共享账户锁要求同进程。

**影响与代价：** 只移动装配代码；WorkerDependencies 先保留兼容入口，避免 bootstrap 反向 import worker 形成环。shared locks、pool、SandboxManager 仍各构造一次；后台任务 event_types 和 lease 身份不合并。

**验证入口（尚未执行）：** `test/test_web_temporal_contract.py`；`test/test_web_outbox_recovery.py`；`test/web_api/test_research_file_composition.py`；`test/test_workspace_isolation.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 通过原入口 wrapper 保留调用面；按抽取提交回退，无 schema/queue 变更。

## ACD-03 · 生产 Brain/Action 协议与实验包解耦

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / EXTRACT |
| 优先级 / 就绪度 | P1 / DESIGN_READY |
| 逻辑责任域 | Agent contracts（非人员分派） |
| 事实追溯 | H01;H04;H15；E05;E06;E14 |
| 实施门禁 | G03，详见 03 |

**建议：** 将 ActionRequest/BrainDecision/ActionResult 定义迁入现有 common 下拟建 agent_protocol.py；生产调用方直接引用新模块，agent/protocol.py 暂作同一类型对象的重导出。实验实现继续保留且禁止生产 multi。

**依据及收益：** 生产导入 agent.protocol 时会执行 agent/__init__.py 的实验重导出；独立合同能减少实际 import 耦合，同时保留实验代码访问路径。

**备选取舍：** 不采纳整个 agent/ 归档或删除；不在 brain 与 actions 各复制 DTO；不在本轮顺带迁移所有 agent 类型。

**影响与代价：** 需要核对类身份、序列化/反序列化与旧 import。重导出不能写成新的子类；生产新路径不再经过实验包。

**验证入口（尚未执行）：** `test/test_action_result_semantics.py`；`test/test_durable_agent_contract.py`；`test/test_qq_execution_compat.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 保留旧 import wrapper，恢复生产 import 即可回退；若外部序列化依赖 __module__，先保留原定义并另评迁移。

## ACD-04 · 按状态对象确定权威来源

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / KEEP_BOUNDARY |
| 优先级 / 就绪度 | P0 / DESIGN_READY |
| 逻辑责任域 | State ownership（非人员分派） |
| 事实追溯 | H02;H14;H17；E06;E07;E11;E15;E20 |
| 实施门禁 | G03;G10，详见 03 |

**建议：** 保持 PostgreSQL Web/身份/durable/Research/file 状态，QQ Redis/WAL/归档、SQLite workspace metadata、Git workspace、tenant objects、Run scratch 和 Hindsight 的独立所有权。共享 ID 和适配接口，不合成万能 SessionStore。

**依据及收益：** 同名 session/workspace 不等于同一数据实体。合并存储会触及恢复、保留期、事务与文件权限，当前没有重复权威源的充分证据。

**备选取舍：** 不采纳 SQLite 或 QQ WAL 无条件迁入 PostgreSQL；不采纳让 Redis/Trace/Hindsight 决定 Web Run 终态。

**影响与代价：** 声明“逻辑写入 owner”不代表新增数据库或所有 SQL 必须搬进 src/persistence。跨域写入需显式桥接及既有事务边界。

**验证入口（尚未执行）：** `test/web_api/test_sse_gateway.py`；`test/test_workspace_isolation.py`；`test/test_run_file_workspace.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 边界声明无数据迁移；后续任何存储合并单独提供回填、双读校验与回退方案。

## ACD-05 · 文件领域按规则、事务和执行分层

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / EXTRACT |
| 优先级 / 就绪度 | P1 / CONDITIONAL |
| 逻辑责任域 | File capability（非人员分派） |
| 事实追溯 | H08;H14；E07;E08;E16 |
| 实施门禁 | G03;G10，详见 03 |

**建议：** 保留 file_domain/file_runtime/file_adapters 的能力分层。优先将 persistent.py 中 PersistentFileRepository 与 PersistentWebFileService 分开；Repository 可落在 file_domain/persistence.py，服务迁入 file_runtime/persistent.py，值对象留在 domain。审批合同与 Temporal 信号寻址后续分离。

**依据及收益：** persistent.py 同时写 SQL 并调用 OutputPublisher；approvals.py 依赖 Workflow ID 与 CommandResult。只换包名不会切断事务/执行耦合。

**备选取舍：** 不采纳全目录合并为 files；不采纳所有 Repository 汇入一个总文件；不抽象万能 FileService。

**影响与代价：** 迁移前检验 file_runtime/__init__ 重导出依赖环。保留 approval+Outbox、CAS revision、operation_id、lineage 与文件对象发布恢复；跨 PG/文件系统不伪称单一原子事务。

**验证入口（尚未执行）：** `test/web_persistence/test_persistent_web_files.py`；`test/web_persistence/test_file_action_approvals.py`；`test/web_persistence/test_file_lineage.py`；`test/test_tool_execution_approval_temporal.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 先留旧路径重导出并复用原事务实现；不改表与 operation_id，可逐段回退。

## ACD-06 · 保留 Research 固定工作流及领域 Repository

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / KEEP_BOUNDARY |
| 优先级 / 就绪度 | P1 / DESIGN_READY |
| 逻辑责任域 | Research（非人员分派） |
| 事实追溯 | H07；E09;E10;E11 |
| 实施门禁 | G03;G04，详见 03 |

**建议：** 保留 research_domain / research_activities / research_adapters 的分工和固定 ResearchReportWorkflow。将 research_domain/persistence.py 明确登记为领域 Repository，而非仅因 domain 命名就搬迁全部 SQL。

**依据及收益：** 固定阶段、证据/引用、daily diff 与计划调度有独立产品合同；当前分层允许领域内聚的 Repository。

**备选取舍：** 不将 Research 改成 PlanAndExecute strategy；不同时运行 JSON user_reminder 与 Research Schedule 的统一新调度器。

**影响与代价：** 保持 PG task 为 schedule desired state、Temporal Schedule 为执行投影；任何阶段重排都需 History 审查。

**验证入口（尚未执行）：** `test/test_research_temporal_contract.py`；`test/test_research_schedule.py`；`test/web_persistence/test_research_r0_r2.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 本次保留现状；后续阶段变化保留版本分支，不能靠回退文件消除已写入的 History。

## ACD-07 · 统一成果所有权而保留两类发布流程

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / KEEP_WITH_BRIDGE |
| 优先级 / 就绪度 | P1 / DESIGN_READY |
| 逻辑责任域 | Artifact + Research（非人员分派） |
| 事实追溯 | H09；E10;E11;E12 |
| 实施门禁 | G10，详见 03 |

**建议：** Artifact 负责 artifacts/artifact_versions 合同；Research 负责报告内容及引用。将 publish_research_artifact 明确登记为受控跨域发布桥，保留 SQL 事务；聊天继续异步 build，Research 继续发布已生成报告。

**依据及收益：** 同为 artifact_versions 的写入者不等于业务流程重复。Research SQL 同时关联 research_reports，改为另一个异步事件会引入中间状态。

**备选取舍：** 不让 Research 绕行聊天模型重新生成报告；不强推统一 Outbox。若以后新增第三种发布方，再评抽取窄 publication adapter。

**影响与代价：** 稳定确定性 UUID、报告版本关联、权限和幂等性；附件仍通过 ResearchMarkdownPublisher/OutputPublisher 发布，不能和 HTML artifact 混同。

**验证入口（尚未执行）：** `test/web_persistence/test_web_artifacts.py`；`test/web_persistence/test_research_r0_r2.py`；`test/web_api/test_research_file_composition.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 当前不迁 SQL；后续桥接抽取调用原函数，保留数据库授权和原子关联后可回退调用层。

## ACD-08 · 配置选项与已实现能力一致

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / ALIGN_CONTRACT |
| 优先级 / 就绪度 | P0 / DESIGN_READY |
| 逻辑责任域 | Configuration + channels（非人员分派） |
| 事实追溯 | H01;H12;H16；E13;E14;E15 |
| 实施门禁 | G03，详见 03 |

**建议：** 建议 ChannelsConfig 缺省改为 napcat 与仓库 YAML 对齐，显式空列表保留为无渠道模式；显式不支持的渠道在启动前给出配置错误。single 模式不再解析实验 agents.yaml，生产 multi 继续拒绝。保留 fake executor 的 development 门禁。

**依据及收益：** console 当前被静默 skip；single 模式却依赖实验配置解析。用户能配置的能力应与工厂和启动合同一致。

**备选取舍：** 不为迁就缺省值新建 Console 生产通道；不启用 Multi-Agent；保留现状只改注释不能解决配置错误。

**影响与代价：** 这是有意行为变更，非纯重命名：旧 console/未知渠道配置会从告警变成启动错误；需配置迁移说明及显式空列表测试。session_worktree 的处置归 ACD-16。

**验证入口（尚未执行）：** `test/test_model_configuration.py`；`test/test_worker_identity_config.py`；`test/web_api/test_config.py`；`test/test_file_capability_config.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 按配置合同独立提交回退；保留原配置备份，生产 multi/fake 门禁不放开。

## ACD-09 · Web legacy 退役采用分阶段门禁

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / DEFER_RETIREMENT |
| 优先级 / 就绪度 | P0 / BLOCKED_ON_EVIDENCE |
| 逻辑责任域 | Web runtime（非人员分派） |
| 事实追溯 | H03；E01;E02;E22;E23 |
| 实施门禁 | G04;G05，详见 03 |

**建议：** 保留 legacy 新启动能力和所有现有 Workflow/Activity 注册。先验证 durable 默认切换，再停止 legacy 新启动，最后依据运行存量与保留政策评审注册退役。当前不批准删除任何 legacy 实现。

**依据及收益：** 默认 durable=false；已有 frozen History 仅覆盖 legacy completed happy path。关闭 durable 开关只改变未来 Start，不中止已有 durable 或取消审批等待。

**备选取舍：** 不按 legacy 文件名删除；不把单个 replay 通过当全量历史兼容证明；不以把开关改回 false 作为完整 rollback。

**影响与代价：** QQ 仍用 DefaultBrainActionLoop，即使 Web legacy 退役也不能删除共享 loop/harness。必须保留已运行 durable 所需全部子 Workflow、Activity 与队列容量。

**验证入口（尚未执行）：** `test/test_web_temporal_replay.py`；`test/test_web_temporal_contract.py`；`test/test_durable_agent_contract.py`；`test/test_durable_agent_worker_kill.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 可将未来新 Start 切回 legacy；保留旧/新 worker definitions 继续完成存量，数据/schema 及 History 兼容另核对。

## ACD-10 · 旧账号代码和历史 JSON 资产分别评审

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / DEFER_RETIREMENT |
| 优先级 / 就绪度 | P2 / BLOCKED_ON_EVIDENCE |
| 逻辑责任域 | Identity（非人员分派） |
| 事实追溯 | H02；E06;E21 |
| 实施门禁 | G06，详见 03 |

**建议：** PostgreSQL 保持生产身份 owner。account/account_service.py 与 models.py 登记退役候选；scripts/merge-account.py 登记旧 JSON 运维入口。候选保留，等外部调用及历史数据证据后逐文件决策。

**依据及收益：** 未找到生产构造者只支持候选判断；merge-account.py 直接改 JSON，不证明旧类运维可达。代码退役与数据删除是两项不同操作。

**备选取舍：** 不将 UNKNOWN 全删；不把旧 JSON 当成 PostgreSQL 当前权威；不自动执行账号合并脚本。

**影响与代价：** 需核验外部脚本/import、备份恢复及账户绑定映射，迁移后可读性与实际保留要求。

**验证入口（尚未执行）：** `test/web_persistence/test_postgres_account_service.py`；`test/test_worker_identity_config.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 未来先停止引用并保留归档/备份；确认迁移可还原后才处理数据。本轮不改变数据。

## ACD-11 · MCP 按传输、投影与管理拆分

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / EXTRACT |
| 优先级 / 就绪度 | P2 / CONDITIONAL |
| 逻辑责任域 | Tool runtime（非人员分派） |
| 事实追溯 | H06;H15；E17 |
| 实施门禁 | G03;G09，详见 03 |

**建议：** 在现有 adapters 下拟建 mcp_transport_http.py、mcp_transport_sse.py、mcp_transport_stdio.py 与 mcp_projection.py；mcp.py 先保留 MCPToolManager 对外入口及兼容导出。

**依据及收益：** 1545 行同时包含三种 session 和 StructuredTool 投影，存在明确职责切口；离线不能穷尽远端工具行为，不能据此删除传输实现。

**备选取舍：** 不只改文件行数；不把三个 transport 归一成未经验证的新协议栈；不增建通用插件平台。

**影响与代价：** 迁出共同 CachedTool/ToolResult 合同需避免循环 import；连接、keepalive、重连、取消、工具名、参数 schema、durable metadata 保持一致。

**验证入口（尚未执行）：** `test/test_durable_agent_hardening.py`；`test/test_mcp_health_script.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 保持 mcp.py 兼容导出，逐传输迁移；投影合同不变，可独立撤回一个传输提交。

## ACD-12 · 其他热点按行为边界择机整理

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / DEFER_BULK_SPLIT |
| 优先级 / 就绪度 | P2 / CONDITIONAL |
| 逻辑责任域 | API + Agent capabilities（非人员分派） |
| 事实追溯 | H06；E18;E19 |
| 实施门禁 | G02;G03;G04，详见 03 |

**建议：** ACD-02 完成后再按实际改动需要拆 API 路由与 Durable Activity 内部服务。API 保留 app factory/lifespan；Activity 类先保留注册 facade，再分离 context、model/planning、tool 执行逻辑。其余热点保持现状。

**依据及收益：** 这些模块涉及 HTTP 中间件顺序、认证、fencing、预算与幂等；以 LOC 自动拆分收益不足，需先确定重复变更或独立责任边界。

**备选取舍：** 不一次拆完所有 600 行以上文件；不把 Experimental strategies.py 因行数列为生产优先重构；不为拆分复制 store/lease 状态。

**影响与代价：** 每次限一个路由族或 Activity 责任；HTTP path/status/middleware 顺序、Activity name/DTO/超时/operation key 全保留。

**验证入口（尚未执行）：** `test/web_api/test_file_protocol_middleware.py`；`test/test_durable_agent_hardening.py`；`test/web_persistence/test_durable_agent_hardening.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 保留 app 与 Activity 原入口，逐责任切口回退；不更换 History 可见名称。

## ACD-13 · 依赖清单建立单一维护源

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / SINGLE_SOURCE |
| 优先级 / 就绪度 | P1 / DESIGN_READY |
| 逻辑责任域 | Build tooling（非人员分派） |
| 事实追溯 | H13;H17；E24 |
| 实施门禁 | G11，详见 03 |

**建议：** 根 requirements.txt 作为维护源，src/requirements.txt 作为同步副本，增加确定性同步入口与差异校验；保留当前 Docker build context。Document 依赖继续独立。

**依据及收益：** 两份内容相同但都被实际构建使用；维护源可唯一，物理副本暂时不能直接删除。

**备选取舍：** 不使用跨 build context 的软链接或 -r ../requirements.txt；不为去重顺带改变全部镜像上下文。

**影响与代价：** 仅明确编辑路径和一致性检查；同步不运行依赖升级，不重写根 persistence SQL 与 src/persistence Python 层分工。

**验证入口（尚未执行）：** `src/Dockerfile`；`src/Dockerfile.web-api`；`src/Dockerfile.migrate`；`src/Dockerfile.document`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 保留两份文件及原 COPY 路径，回退同步工具不会影响既有镜像构建输入。

## ACD-14 · 架构文档按当前边界纠偏

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / ALIGN_DOCUMENTATION |
| 优先级 / 就绪度 | P0 / DESIGN_READY |
| 逻辑责任域 | Architecture documentation（非人员分派） |
| 事实追溯 | H01;H03;H04;H08;H16；E01;E02;E05;E06;E16 |
| 实施门禁 | G01，详见 03 |

**建议：** 后续更新 single_agent 组件/容器/时序 Markdown，覆盖双 Web 路径、Document Worker、Research 双发布及状态 owner；closure report 标注历史适用范围。准确修正文档/docstring，不借修文档触发代码搬迁。

**依据及收益：** D01–D10 中既有文档落后、配置错位，也有已在 2.1 更正的指导种子；需要不同处置，不能全部标记完成。

**备选取舍：** 不覆盖 2.1 事实地图，不把拟议目标写成当前实现；不改 Excalidraw 内容。

**影响与代价：** 当前审计产物只登记建议；既有文档 drift 仍未关闭。Markdown 更新后显式登记 visual drift，由人工处理视觉文件。

**验证入口（尚未执行）：** `docs/architecture/README.md`；`artifacts/architecture-audit/phase2_1/architecture_drift.csv`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 文档变更可独立撤回，保留历史适用日期/基线及当前代码依据。

## ACD-15 · Research 前端缺口归产品范围决策

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / DEFER_PRODUCT_SCOPE |
| 优先级 / 就绪度 | P2 / BLOCKED_ON_PRODUCT |
| 逻辑责任域 | Web product（非人员分派） |
| 事实追溯 | H07；E09;E25 |
| 实施门禁 | G08，详见 03 |

**建议：** 保留现有 Chat/Trace/Artifact/File 前端范围；明确 Research API 已有、专用任务管理 UI 未交付。是否新增任务 CRUD/手动触发/调度/报告入口另建产品需求。

**依据及收益：** 后端可达与前端产品完整不是同一结论。架构收敛不能自动扩展为 Research 工作台开发。

**备选取舍：** 不删除后端 Research 以匹配 UI；不因 API 存在就宣称 P6 前端闭环；不在此轮增加 UI。

**影响与代价：** 待明确用户流程、权限、调度编辑与失败/恢复体验后再确定路由/store 切分；不能仅凭 App.tsx 小就判定产品完整。

**验证入口（尚未执行）：** `web/src/App.tsx`；`web/src/store/workbench.ts`；`src/web_api/app.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 本次无界面变化；后续 UI 可通过路由/功能入口逐项撤回，保留后端合同。

## ACD-16 · 冻结已实现部署拓扑并阻断虚假能力承诺

| 项目 | 审计结论 |
| --- | --- |
| 状态 / 处置 | PROPOSED / KEEP_TOPOLOGY |
| 优先级 / 就绪度 | P0 / DESIGN_READY |
| 逻辑责任域 | Runtime + workspace（非人员分派） |
| 事实追溯 | H08;H11;H14；E02;E15;E16 |
| 实施门禁 | G03;G07，详见 03 |

**建议：** 当前支持的 Agent 运行形态限定为 QQ/Web 共进程共享 AccountLockRegistry；API 与 Document Activity 继续独立。建议 session_worktree 在生产启动校验显式拒绝为未实现，保留枚举用于未来设计，standalone 暂不作为支持部署。

**依据及收益：** 现有 validator 接受 session_worktree，但 WorkspaceIsolationRuntime 仅在 single_process 模式拿进程锁；参数合法不等于拥有跨进程分支隔离。

**备选取舍：** 不新增 standalone Compose service；不删除 Document 独立进程；不声称数据库 lease 已替代整个文件系统隔离。

**影响与代价：** 收紧 session_worktree 是有意配置合同变更，需更新当前“结构可接受”测试与说明；启动报错应指向已支持的共进程配置。

**验证入口（尚未执行）：** `test/test_web_temporal_contract.py`；`test/test_workspace_isolation.py`；`test/test_document_worker.py`。具体新增场景及通过标准见 03，不能用文件存在替代行为验证。

**回退：** 回退代码时仍只部署已验证 single_process 模式；未来开放独立拓扑必须另验跨进程隔离，不能只撤销拒绝校验。
