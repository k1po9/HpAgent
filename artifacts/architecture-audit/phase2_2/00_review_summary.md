# Phase 2.2 · Architecture Consolidation Decision Review

**架构收敛决策审计｜2026-09-14｜状态：建议稿，待逐项评审。**

结论：HpAgent 下一步应优先收敛**职责归属、组合方式与能力合同**。保留有业务和恢复语义依据的执行、状态与进程边界；先整理 Worker 组合、生产协议和配置错位。当前证据不支持直接删除 Web legacy、合并全部 session/workspace，或开放独立 Web Agent 进程。

## Phase 2.2 的定义与交付边界

Phase 2.1 回答“有哪些模块、怎样可达、哪些事实未闭合”。Phase 2.2 在这些事实之上回答“应保留什么、收敛什么、为何如此、代价是什么、何时可以改变”。每个结论须连接到事实、备选方案、影响、实施门禁和回退方案。

本次交付的是可评审的架构决策集，不是实施完成报告。所有 ACD 记录均为 PROPOSED；没有代替维护者签署采纳，没有修改生产代码、配置、旧架构文档或 Excalidraw。目录中的未来文件名是建议落点，尚未创建。2.1 的 UNKNOWN 和外部证据缺口继续保留。

## 基线与证据强度

| 项目 | 本次记录 |
| --- | --- |
| 2.1 代码基线 | `4ab07fbddf33e9a0dc652833acd9edd519e21fea` |
| 本次 HEAD | `608ef1abdc6aa054987d27304d372c4f36900ffa` |
| 分支 | `feat/hpagent-web` |
| 基线差异 | 两个提交之间仅新增 phase2_1 审计产物；业务代码没有差异 |
| 初始工作区 | phase2_1/03_architecture_truth_table.md 一行 Markdown 表格格式修改；已原样保留 |
| 输入范围 | 2.1 七份 Markdown、相关 CSV/manifest，当前组合、分派、协议、状态、文件、Research、MCP、配置与测试源码 |
| 本次证据 | 25 个直接复核锚点；17 项 H、10 项 D 和全部 83 项 U 均映射到处置 |
| 未验证 | 真实部署开关、在线 History 存量、历史账号数据、外部工具行为、产品范围承诺 |

`audit_manifest.json` 冻结输入与受保护文件哈希。`validation_report.json` 只报告本次审计产物一致性；2.1 中的 lint/typecheck/Compose 检查结果没有冒充本次执行结果。

## 核心决策

| 方向 | 建议 | 决策 |
| --- | --- | --- |
| 执行主线 | 共享 Brain/Action 等能力，保留 QQ/legacy、Durable Web 和固定 Research 控制流；Web 新恢复能力优先进入 durable | ACD-01 |
| 组合入口 | 先抽取 Web 装配，再抽取共享依赖构造，保持同进程共享资源和启动/关闭顺序 | ACD-02 |
| 生产协议 | 将 Brain/Action DTO 脱离实验 agent 包，旧路径作同类型重导出 | ACD-03 |
| 数据与文件 | 按对象和事务定义 owner；渐进拆开持久文件规则、Repository 和执行服务 | ACD-04、05 |
| Research / Artifact | 保留固定 Research；SQL 发布是显式跨域桥，聊天仍走 build Outbox | ACD-06、07 |
| 配置 / 拓扑 | 渠道缺省与工厂对齐；生产拒绝未实现 session_worktree；保留独立 Document Activity Worker | ACD-08、16 |
| 兼容 / 旧资产 | 暂缓 legacy 注册退役和旧账号资产清理，分别补 History、部署及数据证据 | ACD-09、10 |
| 次级整理 | MCP 按传输/投影切分；其余热点按改动需要处理；requirements 维护源唯一但保留构建副本 | ACD-11、12、13 |
| 文档 / 产品 | 架构漂移逐项纠偏；Research 专用 UI 作为单独产品范围决策 | ACD-14、15 |

当前**没有获准删除的文件、Workflow、Activity、表或历史数据**。这里的“没有”源于证据与决策结果，并非要求先将所有 UNKNOWN 清零才能开展任何重构。组合、协议和文档工作可在各自门禁内先行。

## 建议的首批工作

1. 先用 ACD-14 的 D01–D10 处置表修正架构事实表达，保留历史说明及人工视觉漂移记录。
2. 单独实施 ACD-08/16 的配置合同修正，明确其行为变化与配置迁移说明；不要混入机械搬文件。
3. 分别实施 ACD-03 协议解耦、ACD-02 Web 组合抽取、ACD-13 依赖维护源，每项独立验证、独立回退。
4. 在文件事务与 MCP 行为证据齐备后再处理 ACD-05/11；Durable 默认切换与退役按 ACD-09 单独评审。

这是一组建议工作包，未开始执行。详细依赖、完成标准和允许回退的边界见 03。

## 阅读顺序

- [01 决策登记册](01_decision_register.md)：16 项完整建议、备选取舍、影响和回退。
- [02 目标边界](02_target_boundaries.md)：执行主线、逻辑 owner、30 个后端包及前端/部署的归属。
- [03 实施序列与门禁](03_delivery_sequence_and_gates.md)：可拆工作包、11 项验证门禁、兼容退役条件。
- [04 事实到决策的追踪](04_fact_to_decision_review.md)：H01–H17、D01–D10 和未决项的处理原则。
- [05 证据索引](05_evidence_index.md)：源码复核位置与证据边界。
- 机器可读表：`decision_register.csv`、`evidence_register.csv`、`hypothesis_decisions.csv`、`drift_dispositions.csv`、`unresolved_dispositions.csv`、`hotspot_dispositions.csv`。

## 审计完成标准

本轮完成意味着：每项建议有事实、取舍、影响、验证与回退；所有原假设、漂移、未决项都有明确处置；输出引用及基线可校验；审计范围外文件保持原样。完成不要求选定生产切换日期、拿到全部外部证据或实现全部建议。采纳结果应逐项更新 ACD 状态并记录评审日期、责任人和实施链接。
