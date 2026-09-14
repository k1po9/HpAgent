# 未生产阶段的主动退役计划

> Historical architecture evidence. Not current architecture documentation.
> TARGET DECISION / NOT IMPLEMENTED。本文件及 retirement_candidates.csv 只定义后续删除范围与前提，本轮没有删除源码、测试或数据。

## R1 门禁为何作废

用户明确不存在需兼容的旧生产数据、线上 Temporal History 或旧部署。R1 的 U-HISTORY/U-ACCOUNTS 生产存量问题不再阻塞目标重构。这是 `PREMISE_SUPERSEDED`，不是“查询了线上并证明存量为零”。原 2.1 事实及 UNKNOWN 不被修改。

新的 G05：**新 canonical 双入口路径成立 → 每个拟删对象不再是目标必需/可达 → 目标测试与全新安装通过 → 删除旧实现 → Git 保留历史。** 不设观察周期、不保留永久双注册/feature flag、不新建 legacy/old/deprecated 源码目录。

## 删除单位与依赖救援

| 候选 | CURRENT FACT | TARGET / 删除前必须完成 |
| --- | --- | --- |
| WebRunWorkflow 与 legacy execute_agent Activity | 默认 Web 仍可启动；旧模块同时提供多个目标业务引用的常量/DTO | 保留并中立化 durable 生命周期；迁出 queue/FailureInput 等共享合同后删 legacy 定义、注册与调用 |
| QQExecutionHost / WebExecutionHost / Facade / DefaultBrainActionLoop | QQ/legacy 真正运行；facade.py 还有 StableExecutionFailure/EventSink 等被 durable/Trace 使用的合同 | QQ 接 PG+Outbox+durable 后删旧 Host/loop；先迁出正式错误/事件/执行合同，不能按文件名整删依赖 |
| orchestration/workflow.py 的 QQ 长会话 Workflow / process_turn | account mailbox 与 Session 生命周期混合；worker 注入旧 Host | Session/Run/消息规则转入统一领域，保留产品需要的归档/记忆/定时能力后删旧 turn 编排 |
| harness 旧 QQ turn 依赖 | activities.py 同时包含 archive/reflection/metrics，context_builder/prompts 仍供正式上下文使用 | 删除或替换旧 turn 注入；把需要的 prompt/context/反思/指标职责归属明确，不能整包判废弃 |
| QQ SessionStore / WAL 权威链 | application/memory 与 actions 等仍有引用 | 全部正式会话读写转 PG，长期记忆/归档重接统一来源后删除 QQ 专用存储路径；不保留双写 |
| agent 实验/Multi-Agent | agent/__init__ 导入实验类，protocol.py 是正式依赖 | 救出协议后删除实验实现/重导出/配置/仅服务实验的测试。Blackboard 按实际符号/用途定位，不捏造一个不存在的文件 |
| JSON AccountService / models / merge-account.py | PG 身份已使用，旧类无已证构造者，脚本直接改 JSON | 核对目标引用/运维用途并删除旧源码/工具；不做不存在的旧生产身份回填 |
| durable_agent_enabled 与迁移分派 | 双启动和 AlreadyStarted 同时兼容两类型 | 目标使用一种启动类型、一组必要定义；删 flag、旧 fallback、环境变量、Compose/文档残留 |
| obsolete migration compatibility | 名称相似不代表无用；当前 schema 依靠 migrations/runner/函数/授权创建 | 逐对象识别真正兼容残留；必要 schema 创建链保留，或单独 clean-install 验证后重建 baseline |

machine-readable retirement_candidates.csv 用具体路径/符号及 gate 记录相同前提；“REPLACE”表示必要能力被中立实现替代，“EXTRACT_THEN_DELETE”表示需要先保全合同。候选不是今天可以执行的删除列表。

## 负证据要针对拟删对象

逐项检查正式 composition、HTTP/QQ ingress、Workflow/Activity 注册、动态工具/回调、配置/Compose、必要 scripts 和 import/export；移动共享合同后检查旧路径不再被这些入口引用。除静态查找外，在目标 E2E 路径与 registry 测试中证明执行只经过 canonical 链。文件名/LOC/UNKNOWN 不能单独支持删除。

若原实现包含仍需能力，先分离再删除；若仅被旧合同测试引用，先把业务断言迁到目标测试，再移除旧测试。删除非目标实验测试是范围收敛，删除身份/取消/幂等回归场景而不补目标测试则不满足门禁。

## 旧测试与历史资产

`test/test_web_temporal_replay.py` 和 completed legacy fixture 可以随旧 Workflow 合同退役；新增 canonical replay 证明目标历史的确定性，不将新 History 覆盖旧文件后宣称兼容成功。当前 `test/test_durable_agent_contract.py` 的双注册断言需改成目标 registry，而非要求永久保留 legacy 来使旧断言通过。

Git 保存源码、旧 fixture 和普通过期设计。重要决策未来进入 docs/adr；architecture audits 明确历史类别，正式 docs/architecture 随 HEAD 更新。本轮仅改 phase2_2，phase2_1/正式文档/测试的历史标记与清理留到后续。

代码删除许可不意味着任意删除个人 .data、Git 工作区或未提交文件；后续实现通常只需重建已知开发 DB/Temporal 夹具。不需要为这些开发夹具制造旧生产迁移基础设施。
