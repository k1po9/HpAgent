# 实施顺序与验证门禁

这是后续实施的建议拆分，未运行其中的发布、迁移或生产验证。ACD 提案可以逐项采纳；一个条件项未通过，不应阻止无关的文档或局部组合整理。

## 可独立评审的工作包

| 工作包 | 内容 / 依赖 | 最小交付与停止边界 | 验收 / 回退 |
| --- | --- | --- | --- |
| W0 | ACD-14；无需实施依赖 | 更新 D01–D10 的事实表达与处置状态；仅修改对应 Markdown/docstring，标记人工 visual drift | G01；按文档提交回退 |
| W1 | ACD-08、16；以 W0 合同为依据 | 渠道默认/显式不支持值、single 不加载实验配置、生产拒绝 session_worktree；附配置迁移说明 | G03；不要把默认变更伪装为无行为重构 |
| W2 | ACD-03；可独立于 W1 | 一个生产协议定义、新生产 import、旧路径同类型重导出；不归档实验实现 | G03；旧入口仍可用，单独回退 |
| W3a | ACD-02；依 ACD-01/04/16 边界 | 仅迁移 WebWorkerComposition/compose_web_workers；旧入口 wrapper；依赖类型放中立位置避免循环 | G02、G03；按抽取提交回退 |
| W3b | ACD-02；W3a 完成后 | 迁共享依赖构造；保留进程启动/关闭顺序和所有共享实例；不改 worker 数量/queue | G02、G03；依赖所有权比较通过后停止 |
| W4 | ACD-13；独立 | 根清单维护源、src 同步副本、同步命令/差异检查；不升级依赖 | G11；原 COPY 路径保留 |
| W5 | ACD-05；W3 后优先 | 先拆 persistent Repository 与执行服务；保持事务，独立评审批信号合同拆分 | G03、G10；不改表/操作 ID，逐段回退 |
| W6 | ACD-11；G09 可验证后 | 每次迁一种 MCP transport 或工具投影；保留 manager 外部入口 | G03、G09；逐传输回退 |
| W7 | ACD-12；W3 后有具体变更需求时 | 一个 API 路由族或一个 Activity 责任；其余热点不动 | G02、G03、必要的 G04；入口兼容回退 |
| M1 | ACD-09；独立迁移评审 | 仅在 G04 通过且默认切换获采纳后，将 durable 用于未来 Start；仍双注册 | 未来 Start 可切回 legacy；存量 durable 必须继续服务 |
| M2 | ACD-09；M1 运行证据齐备 | 停止 legacy 新 Start 后观察/盘点存量；单独评注册退役 | G05；未通过则长期保留兼容注册 |
| M3 | ACD-10；不依赖其他重构 | 旧类、运维脚本、历史 JSON 三份清单与迁移核验 | G06；不把代码归档等同数据删除 |
| P1 | ACD-15；产品需求明确后 | Research UI 范围、API 消费及体验验收另列 | G08；不计入本次架构整理完成率 |

ACD-01/04/06/07 主要是后续工作必须保持的边界，不需要为了“实施决策”制造额外代码提交。P0/P1/P2 是优先级，不是全部按编号执行的瀑布计划。没有工作量工时或性能收益的实测依据，本轮不编造百分比、耗时或收益分数。

## 门禁清单

“已有测试入口”只说明可以从哪里开始；本次没有执行生产测试，也没有断言这些文件已覆盖新增场景。实施时对改动关联测试执行并记录 PASS/FAIL/SKIP，SKIP 不能当作外部证据通过。

| Gate | 适用行动 | 必须证明什么 / 通过标准 | 已有入口与需补证 |
| --- | --- | --- | --- |
| G01 | 审计/文档 | 审计引用、H/D/U 映射完整且唯一，范围外哈希一致；事实与目标分开。文档实施还需逐条复核 drift，人工视觉单列 | 本目录 audit_tools/validate_review.py；docs/architecture/README.md；自动检查不替代语义评审 |
| G02 | 组合/API 抽取 | 对 real-agent gate 开/关构造结果比较：Worker/Activity 注册、队列与共享对象身份不变；启动失败/关闭后任务和资源释放相同；Outbox event_types/lease 所有权不交叉 | test/test_web_temporal_contract.py、test/test_web_outbox_recovery.py；需要补同实例断言、异常启动/取消关闭场景，不能只更新 import |
| G03 | 公共合同/配置/路径变化 | DTO 字段及类身份、HTTP path/status/认证与中间件次序、Activity name/queue/输入、operation ID 算法按变更范围不变；配置的有意改变用明确 before/after 断言 | test/test_durable_agent_contract.py、test/test_action_result_semantics.py、test/web_api/test_config.py、test/test_workspace_isolation.py；W1 需新增缺省/空列表/不支持渠道及损坏 agents.yaml 在 single 下不影响启动场景；session_worktree 结构接受断言须按新合同调整 |
| G04 | durable 默认切换及语义调整 | 真实 PG+Temporal 验证 legacy/durable 各自的完成/失败/取消、审批等待与恢复、lease/fencing、工具不确定副作用、预算耗尽、worker kill、Start 已接受但 Outbox 未 ack 后翻旗重试；结果与既定期望一致，不能重复最终副作用或遗留永久 busy | test/test_durable_agent_temporal_integration.py、test/test_durable_agent_worker_kill.py、test/web_persistence/test_durable_agent_activity_worker_kill.py、test/test_tool_execution_approval_temporal.py；现有测试不自动构成完整场景矩阵，需补两方向 flag 翻转及真实部署配置记录 |
| G05 | Workflow/Activity 兼容退役 | 每个部署/namespace 的新启动配置、待派发 Outbox、未完成/等待审批/重试/可 reset 执行及版本存量可解释；按保留、reset、归档恢复与回滚窗口决定注册保留期；代表性历史 replay 与旧/新部署回滚演练通过 | test/test_web_temporal_replay.py 只有 legacy completed fixture；需要部署存量查询与相关 durable/审批/取消历史。不能只看 open workflow=0，不能刷新旧 fixture 掩盖失败 |
| G06 | 旧账号代码/数据退役 | 分别核对生产/运维/外部 import、脚本调用、JSON 数据映射到 PG 账户/绑定的完整性、备份/恢复与保留要求；代码停用和数据处置分别批准 | test/web_persistence/test_postgres_account_service.py 仅为行为入口；需历史数据盘点和迁移核对，保留 U-ACCOUNTS |
| G07 | 开放 standalone / session_worktree | 实现并验证真实独立 worktree/分支归属、跨进程并发隔离、QQ/Web 同账户与不同账户、崩溃恢复、不丢未提交文件、文件范围与锁生命周期 | test/test_workspace_isolation.py、test/test_workspace_provisioning.py；现有 validator 通过不是此门禁通过。本门禁阻止开放，不阻止 W1 收紧未实现配置 |
| G08 | Research 专用 UI | 确认用户、任务创建/触发/调度/停用/报告访问范围及权限，形成可验收流程；新增路由/store/API 集成测试通过 | web/src/App.tsx、src/web_api/app.py；当前是范围缺口，待产品需求，不标为后端缺失 |
| G09 | MCP 拆分/替换 | 三类 transport 分别验证 connect/list/call/disconnect、取消、断线恢复、错误映射、keepalive；工具 schema/name/required/durable metadata 和结果转换保持；列明已测 provider 的覆盖范围 | test/test_durable_agent_hardening.py 的 metadata 测试只是局部；需 transport 可控假服务/契约样例。只有实际 provider 协议变化才要求对应外部集成；不要求穷尽所有远端工具才能移动文件 |
| G10 | 文件/成果事务抽取 | 实际 PG 下证明审批+Outbox 原子性、版本 CAS、重复 operation 幂等、对象已写而事务失败后的重试恢复、Research report/artifact 关联与权限、lineage/account 隔离不变 | test/web_persistence/test_persistent_web_files.py、test/web_persistence/test_file_action_approvals.py、test/web_persistence/test_file_lineage.py、test/web_persistence/test_research_r0_r2.py；文件发布不等于跨资源事务原子性 |
| G11 | 清单/构建调整 | 两份 requirements 字节一致，同步可重复；main/API/migration 的 build context 可找到输入；Document 保持独立输入；构建/镜像入口和 migration SQL 路径正常 | src/Dockerfile、src/Dockerfile.web-api、src/Dockerfile.migrate、src/Dockerfile.document；实际构建及最小启动验证在实施时记录，不在本轮下载依赖或启动服务 |

## 兼容退役的三个不同决定

1. **选择未来 Start。** 开关控制 Dispatcher 新启动；若 Outbox 消费前切换配置，同一请求的归属也必须能由 AlreadyStarted 恢复。不能把请求入库时刻与 Temporal Start 时刻混淆。
2. **完成现有执行。** 把 durable 开关改回 false 不取消 durable 存量，不能撤掉 agent queue、子 Workflow/Activity、审批信号处理或 data plane。
3. **撤销兼容定义。** 是否保留可 reset/回放历史取决于明确的保留与支持政策。即使所有 legacy 都已终态，也不自动得到删除许可；Web legacy 退役更不代表 QQ 共享 loop 可删。

默认切换需要把拟部署配置、场景结果、仍运行的版本及可恢复部署包放在同一评审中。旧镜像能否读新增 schema/数据也需单独判断；“git revert”不能回滚外部 History 或数据库状态。

## 后续采纳记录模板

每个被采纳 ACD 追加：评审日期、实际责任人、选定方案、相对建议的偏差及理由、依赖 Gate 结果、实施提交/PR、兼容/数据影响、可用回退路径。状态按 PROPOSED → ACCEPTED（或 REJECTED/DEFERRED）→ IMPLEMENTED → VERIFIED 记录；“暂缓的提案”不等于已经执行退役。
