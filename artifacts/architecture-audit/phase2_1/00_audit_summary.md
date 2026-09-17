# Phase 2.1 架构考古与模块摸底

当前实现已整理成全量文件普查、分层模块表、运行主链、数据所有权、17条假设求证和文档漂移证据。**已完成指导第 24 节的离线审计交付要求**。补充了具体依赖绑定、框架回调与状态访问证据；未闭合符号和外部行为仍保留在 unresolved。此状态不表示线上验证或所有动态路径已穷尽。

## 审计基线

- HEAD：`4ab07fbddf33e9a0dc652833acd9edd519e21fea`；分支：`feat/hpagent-web`。
- 与指导文档基线相同；初始工作区干净。
- 没有修改生产代码、既有架构文档或 Excalidraw，只新增本目录审计产物。
- 运行含义：代码上可达，不代表线上已运行；未读取实际部署 .env、Temporal History 或用户历史数据。

## 主要发现

1. `agent/protocol.py` 是生产协议，不能把整个 agent/ 当成实验遗留。Multi-Agent 编排仍被生产启动拒绝；agents.yaml 却仍按文件存在性加载。
2. QQ 和 Web legacy 共享 Facade/DefaultBrainActionLoop；Durable Web 是另一套 Temporal 控制流。旧组件/时序文档没有完整覆盖这个分支，README 和配置参考则已有更新。
3. legacy/durable 新启动在 dispatcher 分流，definition 始终双注册。当前默认 durable=false，因此 legacy 仍接收新请求；不能按“旧代码”归档。
4. Document Workflow 在 Web lifecycle Worker，重型 Activity 在独立 Document Worker，queue/进程边界不同。
5. Research 是固定 Workflow；后端 API/手动触发/调度链存在，当前前端没有 Research 任务管理入口。Research Artifact 发布也不走聊天 Artifact Build 的统一路径。
6. SessionStore、SQLite WorkspaceDB、Git workspace、RunFileWorkspace、tenant file store 和 Hindsight 分别拥有不同状态，目录相近不表示重复。
7. ChannelsConfig 缺省 console，但 factory 不支持；仓库 YAML 选择 napcat。Standalone Web Worker 在当前 Compose 缺省拓扑下拒绝启动。
8. 两份 requirements.txt 字节相同，但分别被不同 Docker build context 使用。最大的后端文件是 MCP adapter（1545行），已纳入热点而非仅沿用指导列出的名单。

## 数量与分类

| 指标 | 数量 |
| --- | ---: |
| 仓库扫描文件 | 1078 |
| 后端 Python 源文件 | 239 |
| 前端 TS/TSX 文件（含 test/types） | 44 |
| Truth rows（package/file/asset/symbol） | 1609 |
| 文件/资产 Truth rows | 530 |
| 静态依赖边 | 4402 |
| 运行结构边（含测试/运维，非线上调用次数） | 10813 |
| 人工补证的关键运行边 | 135 |
| 生产相关逻辑 roots | 7 |
| 命名 roots（含 migration） | 8 |
| Compose services（含测试/运维/基础设施） | 17 |
| 生产 HTTP routes | 42 |
| Workflow / Activity definitions | 14 / 37 |
| UNKNOWN：全部行 / 文件资产行 | 256 / 47 |
| unresolved 条目（符号按文件分组） | 83 |

| reachability_class | 文件/资产行 | 全部 Truth 行 |
| --- | --- | --- |
| PROD_DIRECT | 5 | 64 |
| PROD_CONDITIONAL | 201 | 895 |
| COMPATIBILITY_REGISTERED | 0 | 0 |
| OPS_ADMIN | 26 | 30 |
| BENCHMARK | 12 | 12 |
| DEV_ONLY | 6 | 6 |
| TEST_ONLY | 144 | 144 |
| EXPERIMENTAL_REJECTED | 13 | 126 |
| GENERATED_OR_DATA | 76 | 76 |
| UNREACHABLE_PROVEN | 0 | 0 |
| UNKNOWN | 47 | 256 |


COMPATIBILITY_REGISTERED=0 是单一主分类口径：现存两套 Web Workflow 都有条件新启动路径，兼容注册另以 HISTORY_COMPATIBILITY 标记。UNREACHABLE_PROVEN=0 表示没有声称已完成任何文件的12项负证明。UNKNOWN 包括包初始化/重导出、协议和未闭合具体方法，不能按数量理解为同等数量的废弃模块。

## Fact flags Top 10

| Flag | 文件/资产数 |
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


## 阅读入口

- `01_repository_inventory.md`：范围、计数、方法与验证。
- `02_entrypoints_and_composition_roots.md`：Compose、进程、queue、配置门禁。
- `03_architecture_truth_table.md`：30个后端 package 与各能力模块。
- `04_runtime_reachability.md`：Web/QQ/Research/File/Document/Artifact/Memory 主链。
- `05_architecture_drift_and_fact_flags.md`：drift 与 H01–H17 逐项结论。
- `06_archaeology_and_module_boundaries.md`：git 演进脉络与状态所有权。
- 细粒度事实：`architecture_truth_table.csv`、`dependency_edges.csv`、`runtime_edges.csv`、`repository_inventory.csv`、`unresolved_items.csv`。
- 补证：`reachability_proofs.csv`、`reviewed_runtime_edges.csv`、`temporal_and_dynamic_registrations.csv`、`http_routes.csv`、`outbox_consumers.csv`、`storage_ownership.csv`、`compose_services.csv`、`schema_objects.csv`、`ops_entrypoints.csv`、`hotspots.csv`。
- `audit_manifest.json`：基线、统计、命令、口径与 DoD 缺口。

## DoD 对照

| 要求 | 状态 |
| --- | --- |
| HEAD/分支/初始工作区记录 | 完成 |
| src Python / web TS/TSX 全量 inventory + 文件 Truth row | 完成 |
| process roots / Compose command / queue 分离 | 完成 |
| AST import / Temporal definition与注册 / route→service /主要DI/Outbox | 完成扫描及主链补证 |
| QQ/Web/Durable/File/Research/Artifact/Memory 主链 | 完成 |
| 状态存储与外部依赖 ownership | 主要边界完成；逐符号传递副作用未穷尽 |
| 热点 symbol 下钻 | 全量定义已记录；部分符号运行链未闭合 |
| production/test/dev/ops/benchmark/experimental 区分 | 完成；证据不足用 UNKNOWN |
| 旧 closure 结论重新验证 / drift / H01–H17 | 完成 |
| unresolved、负证据与保护范围 | 已显式记录，无删除/合并/拆分决策 |
| typecheck / lint / compose 配置 | 全部通过 |
| git diff --check / 修改路径限制 | 见 manifest 最终完整性校验 |

## 使用边界

这套产物可用于理解当前架构、定位模块、选择下一步证据补齐点。没有闭合的符号/数据路径不得直接支持删除、兼容退役或迁移决策。部分文件/热点符号的构造者、动态调用和数据读写字段仍未闭合，不能把目前的全量行覆盖标记为完整事实证明。

## 继续审计补证

新增 77 项具体 receiver 绑定、2115 条细化边记录，累计运行图为 10813 条去重边。闭合了 26 组待确认条目的全部或部分内容；UNKNOWN 从 421 行降至 256 行，其中 47 个文件/资产行。剩余 83 项包含结构性声明、动态符号与外部运行证据，不等同于 83 个废弃模块。

`composition_bindings.csv`、`refined_runtime_edges.csv` 和 `resolved_items.csv` 记录补证依据；`state_effects.csv`、`sql_function_edges.csv` 将 SQL 直接访问、存储函数和词法候选分开，并区分 PostgreSQL 与 SQLite。传递字段是受配置门禁约束的图并集，可能同时包含互斥分支，不代表单次执行轨迹。

首版把逐符号动态路径及外部副作用全部穷尽当作完成门槛，严于指导第 24 节。此次按该节逐项核对：要求扫描并登记无法确认项，并非 UNKNOWN 清零。保留的证据边界见 manifest 的 `evidence_limits`。
