# 从事实地图到架构决策

## 转换规则

事实分类不直接等于处置动作。PROD_CONDITIONAL 支持“保留相关入口与合同”，不禁止重构；EXPERIMENTAL_REJECTED 支持隔离，不能推出可删；UNKNOWN 支持继续补证，不是死代码标签；LARGE_HOTSPOT 只触发职责审视，不能自动获得拆分优先级。

每项决策遵循：事实 H/E → 需要解决的职责或合同问题 → 推荐与被放弃的方案 → 兼容/数据/行为影响 → G 门禁 → 可回退工作包。未把行数、依赖数、历史提交标题当成收益量化；当前未观察到的性能问题不作为拆进程依据。

## H01–H17 的处置

| 假设 | 对应决策 | 转换结果 |
| --- | --- | --- |
| H01 | ACD-03;ACD-08 | 抽取生产协议并对齐实验配置合同；保留实验代码 |
| H02 | ACD-04;ACD-10 | 生产身份归 PG；旧类与旧 JSON 分别暂缓退役 |
| H03 | ACD-01;ACD-09 | 保留双启动与双注册；迁移/退役分阶段 |
| H04 | ACD-01;ACD-03;ACD-09 | 保留 harness/loop 生产职责，不跟随 Web legacy 整包删除 |
| H05 | ACD-02 | 先 Web 装配、后共享依赖；保持启动关闭 owner |
| H06 | ACD-11;ACD-12 | MCP 有切口；其他热点按行为和需求安排 |
| H07 | ACD-06;ACD-15 | 保留固定 Research 与领域 Repository；UI 另定产品范围 |
| H08 | ACD-05;ACD-16 | 文件分层渐进整理；Document Activity 保持独立 |
| H09 | ACD-07 | 明确 Artifact owner 与 Research 原子发布桥，不合并业务流水线 |
| H10 | ACD-02;ACD-06 | 保持提醒注册/poll gate 与 Research Schedule 区别 |
| H11 | ACD-16 | 当前冻结共进程拓扑，开放 standalone 需实证 |
| H12 | ACD-08 | 保留 fake executor 开发门禁，不以 import 判为生产业务 |
| H13 | ACD-13 | 维护源统一，保留两个真实构建输入 |
| H14 | ACD-04;ACD-05;ACD-16 | 按状态生命周期保留 owner，不强行合并 store |
| H15 | ACD-01;ACD-03;ACD-11 | 保持 Model/Tool 分离，收敛协议及 MCP 职责 |
| H16 | ACD-08 | 修复缺省与工厂错位，未知/不支持配置显式失败 |
| H17 | ACD-04;ACD-13 | 保留 SQL 资产与 Python runner 的合理分层 |

## D01–D10 的处置

| 漂移 | 对应决策 | 处置 | 本次状态 |
| --- | --- | --- | --- |
| D01 | ACD-01;ACD-14 | 把单 loop 唯一性限定 QQ/legacy，新增 durable 控制流；登记 visual drift | PROPOSED_NOT_APPLIED |
| D02 | ACD-14;ACD-16 | 补外部依赖/Document Activity 进程，区分测试 profile 与生产拓扑 | PROPOSED_NOT_APPLIED |
| D03 | ACD-03;ACD-14 | 限制 legacy 表述到实验模块，标注 protocol 生产用途 | PROPOSED_NOT_APPLIED |
| D04 | ACD-01;ACD-09;ACD-14 | 区分 legacy/durable、approval/fencing；closure report 保留日期范围 | PROPOSED_NOT_APPLIED |
| D05 | ACD-08 | 缺省 napcat、显式空列表、错误配置启动校验；实施后才能关闭漂移 | PROPOSED_NOT_APPLIED |
| D06 | ACD-12;ACD-14 | 说明含 legacy execute_agent adapter，不为修注释立即拆所有 Activities | PROPOSED_NOT_APPLIED |
| D07 | ACD-04;ACD-14 | 描述 TurnMemory/bootstrap 实际消费者，保留状态实现 | PROPOSED_NOT_APPLIED |
| D08 | ACD-05;ACD-16 | 沿用 lifecycle Workflow + 独立 document Activity 的纠正事实，不创建代码修复 | FACT_CORRECTED_IN_PHASE2_1 |
| D09 | ACD-07 | 沿用两条发布链，不以统一示意图倒推实现合并 | FACT_CORRECTED_IN_PHASE2_1 |
| D10 | ACD-03;ACD-08;ACD-14 | 明确当前总会解析；未来 single 跳过实验定义后同步 agents.yaml 注释 | PROPOSED_NOT_APPLIED |

D08/D09 是指导种子的事实修正，2.1 已更正，不代表既有架构文档全部同步。其余项仅形成处置提案，本次没有将它们标为已修复。Excalidraw 仅允许人工维护，后续 Markdown 变更需显式登记视觉漂移。

## 未决项的阻断范围

83 项 U 原始记录逐行保留在 unresolved_dispositions.csv 中，closed_in_phase2_2 均为 false。本次按决策影响重新分类，没有改写 2.1 的证据状态。

| 类型 | 对下一步的影响 |
| --- | --- |
| 包初始化/协议/重导出 | 保持结构合同；移动 import 时核对对象身份与副作用，不要求所有 DTO 有独立 runtime root |
| 局部动态 receiver/符号调用 | 仅对受拟议变更影响的方法补链；不阻塞其他职责的文档或代码抽取 |
| 部署与 History | 阻止默认切换/退役推断；继续双注册无需额外线上证据 |
| 旧账号资产 | 阻止旧代码与数据删除；不动摇当前 PostgreSQL 身份 owner |
| workspace 拓扑 | 阻止新增独立 Agent 部署；明确未实现配置可以先修合同 |
| MCP 外部行为 | 对变动涉及的 transport 做契约验证；不要求事先列出全世界远端工具 |
| Research 产品范围 | 阻止宣称 UI 交付完成；是否开发 UI 另作产品决定 |

六项具名外部缺口 U-DEPLOY、U-HISTORY、U-ACCOUNTS、U-TOPOLOGY、U-PLUGINS、U-RESEARCH-UI 的责任角色与具体补证见 CSV；角色是建议责任域，尚未指定人员。

## 热点处置

hotspot_dispositions.csv 覆盖 2.1 architecture_truth_table.csv 中标注 LARGE_HOTSPOT 的全部 26 个文件/资产（按 granularity=file/asset 过滤，不包含 symbol/package），逐行记录保留/条件拆分/明确抽取与理由。它沿用 2.1 的分类与 LOC，只对 E 证据列出的热点做了本次直接源码复核，不声称重新证明每个热点的所有方法。

主 Worker 先拆组合；MCP 有 transport/投影切口但需合同验证；API/Activity 按需处理；SessionStore、ModelClient、BrainActionLoop、OfficialQQ 等保留当前职责，等待具体变更证据。测试、benchmark、运维资产不因为行数大被转化成生产架构整改。

## 评审建议与尚未采纳的取舍

可先按边界采纳 ACD-01/04/06/07，再独立讨论 ACD-08 的 napcat 缺省与配置失败策略、ACD-16 的未实现拓扑拒绝行为。ACD-02/03/13 有明确实施切口，ACD-05/11/12 按条件排期。ACD-09 的 durable 演进方向与具体切换日期分开决定；ACD-10/15 保持数据/产品缺口。

这些取舍已给出推荐，不要求先回答所有问题才得到一份决策稿。本审计完成后，后续可以选择一项直接进入详细实施；其余提案仍保留独立状态。
