# ReAct vs Plan-and-Execute 策略实验

## 实验目标

在同一模型、工具、turn budget 和确定性评分器下，对比两种策略在简单、中等、复杂任务上的
成功率、延迟和调用开销，并识别适合按复杂度路由的场景。

## 实验设计

- 任务：简单、中等、复杂各 10 个，共 30 个任务。
- 配对：每个任务分别运行 ReAct 和 Plan-and-Execute，共 60 个正式槽位。
- 配置：MiniMax-M3、provider-default temperature、max turns 5、每项 1 次重复。
- 隔离：每次运行使用独立会话分支和任务目录。
- 评分：精确 `<answer>`、文件内容或本地 pytest，不使用 LLM judge。
- 指标：可评估成功率、P50/P95 latency、model/tool/planning calls、配对结果和失败类型。

## 数据有效性

60 个槽位均有记录，但 `complex_008`–`complex_010` 的 6 条是隔离仓库污染触发的
`environment_error`，不代表策略能力，故性能分析使用 27 组有效配对。供应商在两个
Plan-and-Execute 简单任务中返回 `model_unavailable`，这些属于 observed result 并保留。

## 结果

| 难度 | ReAct | Plan-and-Execute | 解释 |
|---|---:|---:|---|
| 简单 | 9/10（90%） | 5/10（50%） | ReAct 更快且成功率更高 |
| 中等 | 4/10（40%） | 7/10（70%） | Plan 成功率高 30pp，但平均慢约 41% |
| 复杂 | 2/7（28.6%） | 2/7（28.6%） | 两者均不可靠，Plan 平均约慢 1.90× |
| 总体 | 15/27（55.6%） | 14/27（51.9%） | 单次重复不足以确定全局胜者 |

27 组有效配对中，两者都成功 10、仅 ReAct 成功 5、仅 Plan 成功 4、都失败 8。
Plan-and-Execute 平均模型调用 6.852 次，ReAct 为 3.148 次。

## 证据

- [`report.md`](report.md)：自动生成的分层结果。
- [`summary.json`](summary.json)：可评估口径和配对聚合。
- [`trials.csv`](trials.csv)：标准化 60 槽位表。
- [`trials.jsonl`](trials.jsonl)：append-only 审计记录。
- [`pilots/`](pilots/)：环境 gate、早期延迟试跑和已修复问题记录。
- 运行手册：`scripts/benchmarks/agent_strategy/README.md`。
