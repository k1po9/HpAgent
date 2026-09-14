# Phase 2.2 · Architecture Consolidation Decision Review · 修订 R2

> Historical architecture evidence. Not current architecture documentation.
> 本目录是特定代码基线上的审计与目标决策证据，不代表 HEAD 已实现目标。修订日期：2026-09-14。所有本次目标改造均 NOT IMPLEMENTED。

本次核心原则从原来的“保留合理边界，同时谨慎兼容 legacy”转变为：

> **在未生产前主动收敛为单一 Canonical Architecture：统一 Agent 数据面与 Durable Runtime，删除不再属于产品目标的兼容路径；只保留具有独立业务语义的 Workflow 和 capability 边界，并为 Human-in-the-loop Model Review 与 Workspace 可视化保留稳定扩展接口。**

## 前提与基线

用户明确：HpAgent 尚未正式生产，无需历史生产数据迁移、线上 Temporal History 保留或旧部署兼容。这是用户给出的产品前提，不能用代码推断，也不需要线上盘点才能采纳。本轮只修订 phase2_2，不执行代码重构、测试合同变更、数据操作或旧实现删除。

| 项目 | 记录 |
| --- | --- |
| 当前代码基线 | `4c18f5dff854c31bca1822028eb173bcb6a6055f` |
| 2.1 代码基线 | `4ab07fbddf33e9a0dc652833acd9edd519e21fea` |
| 原 2.2 评审基线 | `608ef1abdc6aa054987d27304d372c4f36900ffa`；旧稿在 Git 提交 `4c18f5d` 中 |
| 代码差异 | 当前 HEAD 与 2.1 基线间无业务代码差异；本次对新增决策相关代码另做定向复核 |
| 原工作区修改 | phase2_1/03_architecture_truth_table.md 的表格对齐；原样保留 |
| 决策覆盖 | 原 ACD-01–16 全部重审，新增 ACD-17/18；用户目标已明确，具体设计取舍仍为审计建议 |

CURRENT FACT 来自 E/H 源码证据；TARGET DECISION 来自用户方向与审计推导；FUTURE OPTION 尚未选定；NOT IMPLEMENTED 表示目标改造未实施。代码当前仍是 QQ legacy、Web 双分流、QQ/PG 两套短期会话状态。修订决策不会使这些事实消失。

## 被推翻与保留的结论

| ACD | 修订结果 |
| --- | --- |
| 01 | 推翻长期两套 Agent 控制流；Web/QQ 共用唯一 durable 主线 |
| 04 | 推翻 QQ Redis/WAL 与 Web PG 长期双 Conversation 权威；核心交互实体统一 PG |
| 09 | 推翻线上存量/观察窗口/长期双注册门禁；目标可工作、旧实现无必要、测试通过后主动删除 |
| 10 | 推翻旧账号生产数据迁移作为删除前置；实验/旧账号/过时兼容主动删除，必要 schema 创建能力保留 |
| 02、03 | 组合整理后移；先救出必需协议，再删旧路径，不长期重导出 |
| 05、06、07、11、13 | 保留 File/Document、固定 Research、Artifact 发布桥、MCP 职责分离与依赖维护源方向，适配新主线 |
| 08、12、14、15、16 | 更新迁移开关删除、模型边界、文档职责、下一阶段查询需求和 worktree 演进候选 |
| 17、18 | 新增 ModelInputSnapshot / Durable Model Review 和 WorkspaceQueryService |

## 新目标架构一页摘要

Web API 与 QQ ingress 只承担协议、身份映射、授权和投递差异。两者调用同一个 Conversation 命令边界，在 PostgreSQL 统一管理 Conversation、Message、Session、Run，并同事务写 Outbox。Dispatcher 启动渠道中立 Agent 生命周期，再进入 AgentRunWorkflow → ReAct / Plan-and-Execute → Durable Activities。取消、恢复、审批、预算、Trace、结果提交共用一套语义。当前 Web 专属名称是改造起点，不是领域归属。

Research 经自己的 command / Run / Outbox 进入 ResearchReportWorkflow，保留固定阶段；其 Run 不需要伪造 Conversation。Heavy Document 保留独立重型 Activity worker。Artifact 保留统一结果身份/版本及有区别的发布流程。Budget、Trace、Hindsight、File、Workspace 继续独立，不吸收进万能 Conversation Store。

每次主 Agent 模型调用经过 PrepareModelInput → immutable ModelInputSnapshot → review 或显式不审阅策略 → InvokeModel。完整请求保存在 PG，Temporal 只存引用和控制信息。权限只影响可见字段，不改变 canonical input；批准绑定 snapshot_id + content_hash。模型 fallback、工具定义或输入变化不能沿用旧批准。最终 provider payload、等待与 lease 语义见 06。

WorkspaceQueryService 提供当前 Conversation 所属工作空间的树、文件、状态、diff 和元数据。查询只收逻辑 ID/相对路径与版本，不能借 GET 切分支或直接浏览 Sandbox 路径。Persistent Workspace 与 Run Files 分区；session_worktree 当前未实现，但成为明确演进候选，查询合同不以其提前实现为前提。

## 后续代码工作包顺序

1. W0 冻结目标与文档职责：ADR 写目标；当前架构文档如实描述 HEAD。
2. W1 唯一 Durable 主线：以现有 durable 演进中立生命周期，先验证 Web。
3. W2 双入口领域收敛：QQ 接入统一 PG 命令/Run/Outbox，补入站/投递与跨入口验证。
4. W3 主动删除 legacy：先迁出目标仍需的协议/常量/记忆能力，再删旧 loop、会话权威、迁移开关和实验实现，更新测试合同。
5. W4 composition / protocol 清理：整理唯一运行时装配；必要协议救援在 W1–W3 完成，避免先删后救。
6. W5 模型能力边界：准备/调用分离、请求快照、审阅等待与权限投影；UI 单独交付。
7. W6 Workspace Query：逻辑绑定、只读版本与范围合同；UI 单独交付，worktree 完整隔离另评。
8. W7 File / MCP / build 整理：按稳定能力边界切分，验证全新构建与安装。

完整退出条件见 03。无需旧生产 History、数据迁移证明或部署观察周期；目标 durable 的 replay / 故障恢复仍是正确性要求。

## 阅读与交付

- [01 决策登记册](01_decision_register.md)：18 项，分列 Current / Target / Required migration/refactor / Future option / 实施状态。
- [02 目标边界](02_target_boundaries.md)：主线、状态 owner、Surface 语义和 30 个包归属。
- [03 顺序与门禁](03_delivery_sequence_and_gates.md)：新的 G05 退役门禁与 W0–W7。
- [04 事实追踪](04_fact_to_decision_review.md)：17 项 H、10 项 D、83 项 U、26 个大型文件/资产。
- [05 证据索引](05_evidence_index.md)：源码锚点及证据范围。
- [06 模型输入审阅](06_model_input_review_contract.md)、[07 Workspace 查询](07_workspace_query_contract.md)、[08 删除计划](08_retirement_plan.md)：后续实现的设计边界。

JSON/CSV 与 Markdown 同步，validation_report.json 只报告本次产物/引用/范围校验，不是业务测试或重构完成报告。2.1 的 UNKNOWN 没有被伪造为已证不可达；不再适用的线上前提在新处置表中明确标为已取消门禁。
