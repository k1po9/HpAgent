# 事实到决策的修订追踪 · R2.1

> Historical architecture evidence. Not current architecture documentation.
> 修订 R2.1；所有目标改造 NOT IMPLEMENTED，代码事实与目标分列。

R2 已修订产品前提与目标处置；R2.1 仅调整 ACD-01/04/17 的三个合同边界与引用，不改 2.1 代码分类。CURRENT 可达不代表 TARGET 应长期保留；UNKNOWN 不代表可直接删除。拟删对象补目标调用/注册证据后主动清理。

## H01–H17

| H | ACD | TARGET disposition |
| --- | --- | --- |
| H01 | ACD-03;ACD-08;ACD-10 | 正式协议先救出，非目标实验/配置后删除；不再永久保留实验包 |
| H02 | ACD-04;ACD-10 | PG 统一身份，旧 JSON 代码经目标消费者核验删除；不需旧生产数据迁移 |
| H03 | ACD-01;ACD-08;ACD-09 | 双启动/双注册是当前过渡事实；W1 先冻结 source-neutral 与 suspend-safe 合同，目标验证后删除 |
| H04 | ACD-03;ACD-09 | QQ 迁入 canonical 后退役旧 loop；harness 必要 context/记忆/定时能力先迁出 |
| H05 | ACD-02 | 唯一主线和 legacy 清理后整理 composition，不先搬旧双栈 |
| H06 | ACD-11;ACD-12;ACD-17 | 按能力拆分，模型 preparation/invocation 成为明确切口 |
| H07 | ACD-06;ACD-15 | Research 固定且独立；共享 Run 不强制 Conversation；UI 另排 |
| H08 | ACD-05;ACD-16;ACD-18 | 保留 File/Document 语义与独立 Activity；workspace query 分 scope |
| H09 | ACD-07 | 结果资产合同统一，Research 原子发布桥与聊天 build 保留 |
| H10 | ACD-02;ACD-06;ACD-09 | 提醒/反思等必要服务从旧 turn 依赖分离，不并入 Research |
| H11 | ACD-16;ACD-18 | 当前未实现 worktree，明确拒绝；作为未来隔离候选而非无需求 |
| H12 | ACD-08 | 保留开发 fake gate；不能以清理 migration 为由删除全部 feature gates |
| H13 | ACD-13 | 维护源唯一，保留必要 build 输入或后续统一上下文 |
| H14 | ACD-04;ACD-16;ACD-18 | Conversation/Message/Session 归 Conversation，Run 为共享 Execution/Lifecycle；PG admission 唯一，busy 为可替换 policy；不同 workspace/file/memory 对象继续分工 |
| H15 | ACD-01;ACD-11;ACD-17 | 统一 Agent，保留 Model/Tool；每个 snapshot 满足冻结 AuthorizationPolicy，人工频率可替换；W1 suspend/resume，W5 接入审阅 |
| H16 | ACD-08 | 渠道默认与支持项对齐；目标无 legacy 编排选项 |
| H17 | ACD-10;ACD-13 | 保留全新 schema 创建，删除 obsolete compatibility 不能按目录名判断 |

## D01–D10

| D | ACD | R2.1 处置 | 状态 |
| --- | --- | --- | --- |
| D01 | ACD-01;ACD-14 | HEAD 仍双路径；目标单 durable，当前文档纠事实、ADR 写目标，随实现再收口 | TARGET_REVISED_NOT_IMPLEMENTED |
| D02 | ACD-05;ACD-14;ACD-16 | HEAD 容器清单补真实 Document/依赖；目标部署仍不冒称 worktree 已实现 | TARGET_REVISED_NOT_IMPLEMENTED |
| D03 | ACD-03;ACD-10;ACD-14 | 当前协议正式使用必须记录；未来先迁协议再删除实验目录，旧 closure 可由 Git/ADR 保存 | TARGET_REVISED_NOT_IMPLEMENTED |
| D04 | ACD-01;ACD-09;ACD-14 | 当前时序补 durable；目标 Web/QQ 单主线；旧稿后续移除或保留重要 ADR | TARGET_REVISED_NOT_IMPLEMENTED |
| D05 | ACD-08 | 按真实渠道能力修合同；目标删除迁移开关，不扩展 Console 实现 | TARGET_REVISED_NOT_IMPLEMENTED |
| D06 | ACD-09;ACD-12 | 旧 Activity 当前含执行 Host；目标删 execute_agent，保留并中立化必要 lifecycle | TARGET_REVISED_NOT_IMPLEMENTED |
| D07 | ACD-04;ACD-09 | 当前 SessionStore 消费链保留为事实，目标迁 PG 后删旧权威，不永久只改注释 | TARGET_REVISED_NOT_IMPLEMENTED |
| D08 | ACD-05;ACD-16 | 沿用 2.1 已纠正的 Document Workflow/Activity 边界 | FACT_CORRECTED_IN_PHASE2_1 |
| D09 | ACD-07 | 沿用 2.1 两发布路径事实，统一资产合同而保留业务流程 | FACT_CORRECTED_IN_PHASE2_1 |
| D10 | ACD-03;ACD-08;ACD-10 | 当前存在即解析 agents.yaml；目标移除非目标实验加载/配置 | TARGET_REVISED_NOT_IMPLEMENTED |

## 83 项 U 的含义变化

unresolved_dispositions.csv 保留每个原 ID/kind/status，source_fact_closed=false。U-DEPLOY/U-HISTORY/U-ACCOUNTS 标记 PREMISE_SUPERSEDED、decision_gate_removed=true：旧生产证据门禁被用户前提取消，绝不是源码/线上事实被证明。其余按目标局部补证、未来拓扑或独立产品范围处理。不要求旧历史数据、外部调用者永远兼容或 UNKNOWN 全清零。

U-TOPOLOGY 仍是当前未实现事实，但加入 session-scoped workspace 演进候选；U-RESEARCH-UI 仍是 Research 专用 UI 范围，不能拿它否认已经明确的 Model Review/Workspace 需求。结构性协议根据目标消费者迁出保全；非目标实现根据 G05 删除。

## 26 个大型文件/资产

hotspot_dispositions.csv 按 2.1 truth table 的 file/asset 粒度覆盖全部 LARGE_HOTSPOT。实验 strategies、旧 BrainActionLoop、QQ SessionStore 改为目标验证后的退役；web_domain/services 提升中立领域；model runtime/client 增加准备/调用冻结切口。各测试保留必要业务回归、替换旧接线合同；benchmark/构建/UI 不因 LOC 自动整改。

## 修订追溯

change_type 保留 R2 相对 R1 的处置类别；R2.1 只修改 ACD-01/04/17，其他 15 个决策对象保持不变。R2 稿由 Git 提交 53007a2 保留；E44/E45 是本轮新增的定向证据。旧 R1 内容由 Git 提交 4c18f5d 保留，本目录只维护一套最新修订稿，不建立冲突的 old/current 两套目标。Machine registers 与摘要/目标/工作包均沿用同一组 ACD 与 R2.1 gate 编号。
