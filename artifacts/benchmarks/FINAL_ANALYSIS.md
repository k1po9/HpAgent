# HpAgent 三组量化实验最终分析

## 结论摘要

本轮完成了 Temporal Durable Agent 故障恢复、Transactional Outbox 宕机恢复和 Agent 策略
对比三组实验。可靠性部分共有 100 次独立观测（50 次 Workflow Worker 故障、20 次 Activity
Worker 故障、30 个 Outbox 请求），全部达到各自预定义的恢复或安全终态，未观察到丢失 Run、
重复 Workflow 或额外重复外部副作用。

策略实验记录了 30 个任务 × 2 种策略。最后 3 个复杂任务的 6 个槽位受到隔离工作区污染，
原始记录保留但不纳入策略性能，最终有 27 组有效配对。整体上 ReAct 为 15/27（55.6%），
Plan-and-Execute 为 14/27（51.9%），不能据此宣称全局胜者；分层结果显示简单任务适合 ReAct，
中等任务更受益于 Plan-and-Execute，当前模型与五轮预算下两种策略对复杂任务都不可靠。

## 实验一：Temporal Durable Agent 故障恢复

### Workflow Worker SIGKILL

- 5 个故障边界，每类 10 次，共 50 次真实进程 SIGKILL。
- 恢复成功 50/50（100%）。
- 出现重复逻辑 operation 的 trial：0。
- 出现重复外部副作用的 trial：0。
- 恢复延迟 P50 10,485.010 ms，P95 10,683.786 ms。

该结果验证 Workflow Worker 丢失后，replacement Worker 能通过 Temporal Event History 恢复。
它不等价于任意外部工具的 exactly-once 保证。

### Activity Worker SIGKILL 与 ack gap

- A1（副作用前）5 次、A2（幂等副作用后 ack 前）5 次、A3（非幂等副作用后 ack 前）10 次。
- 20/20 均观察到真实 Activity retry，并达到预定义安全结果。
- A1/A2 共 10/10 最终完成。
- A3 共 10/10 进入 `uncertain` fail-closed，独立外部计数器均为 1，额外重复副作用 0/10。
- kill-to-terminal 延迟 P50 3,998.718 ms，P95 4,129.283 ms。

A3 是安全失败而非自动恢复成功：系统无法证明首次非幂等调用结果时，选择
`tool_side_effect_uncertain`，阻止第二次外部写入。

## 实验二：Transactional Outbox 宕机恢复

- dispatcher/Worker 不可用期间接受并持久化 30 个请求。
- start event 最终处理 30/30，Temporal Workflow 完成 30/30，Run 终态完成 30/30。
- 丢失 Run 0，重复 Workflow 0。
- replacement Worker 就绪后的 backlog drain time 为 11,916.932 ms。
- 请求最终完成延迟 P50 9,718.245 ms，P95 12,664.568 ms。

该实验使用真实 PostgreSQL transaction、Outbox claim/ack、Temporal Workflow 和 Run 生命周期；
Agent Activity 使用 benchmark fake model，因此结论针对持久化与编排恢复，不代表模型供应商可靠性。

## 实验三：ReAct vs Plan-and-Execute

### 数据有效性

- 配置：MiniMax-M3、provider-default temperature、max turns 5、每个任务 1 次重复。
- 原始设计：简单/中等/复杂各 10 个任务，每个任务同时运行两种策略，共 60 个槽位。
- 可评估：54 条，即每种策略 27 条。
- 排除：`complex_008`–`complex_010` 共 6 条 `environment_error`。前一任务的 Agent 将
  `dates.py` 写到隔离仓库根目录，安全检查拒绝后续任务。这些记录不代表策略失败。

### 分层结果

| 难度 | 策略 | 有效样本 | 成功 | 成功率 | 平均耗时 | P95 |
|---|---|---:|---:|---:|---:|---:|
| 简单 | ReAct | 10 | 9 | **90.0%** | 13.64 s | 20.69 s |
| 简单 | Plan-and-Execute | 10 | 5 | 50.0% | 23.59 s | 31.68 s |
| 中等 | ReAct | 10 | 4 | 40.0% | 18.68 s | 25.46 s |
| 中等 | Plan-and-Execute | 10 | 7 | **70.0%** | 26.32 s | 45.24 s |
| 复杂 | ReAct | 7 | 2 | 28.6% | 28.27 s | 38.53 s |
| 复杂 | Plan-and-Execute | 7 | 2 | 28.6% | 53.71 s | 104.57 s |
| 总体 | ReAct | 27 | 15 | **55.6%** | 19.30 s | 32.80 s |
| 总体 | Plan-and-Execute | 27 | 14 | 51.9% | 32.41 s | 55.46 s |

### 配对和成本分析

- 27 组有效任务：两者都成功 10，只有 ReAct 成功 5，只有 Plan-and-Execute 成功 4，
  两者都失败 8。5 对 4 的不一致配对不足以支持总体优劣结论。
- ReAct 在简单任务领先 40 个百分点，平均耗时低约 42%。
- Plan-and-Execute 在中等任务领先 30 个百分点，但平均耗时高约 41%。
- 复杂任务两者均为 2/7；Plan-and-Execute 平均耗时约为 ReAct 的 1.90 倍。
- 整体有效样本中，Plan-and-Execute 平均模型调用 6.852 次，ReAct 为 3.148 次，约 2.18 倍；
  平均工具调用分别为 3.296 和 2.778，约 1.19 倍。
- 两个 Plan-and-Execute 简单任务因 MiniMax `model_unavailable` 失败。仅排除这两个供应商失败时，
  Plan-and-Execute 为 14/25（56.0%），与 ReAct 的 15/27（55.6%）接近。

### 可解释结论

推荐按任务复杂度路由，而不是固定单一策略：确定性、短链路任务优先 ReAct；需要多步检索和顺序
组织的中等任务可选择 Plan-and-Execute，并接受更高调用数与延迟。复杂代码修复在 MiniMax-M3、
max turns 5 下成功率不足 30%，当前数据不支持将任一策略作为复杂任务的可靠默认值。

## 实验中发现并修复的问题

1. Plan-and-Execute 日志重复传入 `plan_id` 等 correlation 字段，引发 Python `TypeError`；
   已修复并加入回归测试。
2. 正式 runner 输出被父进程缓冲，造成长时间无终端反馈；已改为实时输出并安全处理 Ctrl-C。
3. 中断记录曾被 resume 误判为完成；现保留审计行并只重试未完成 key。
4. Ctrl-C 在 trial 边界遗留夹具时，隔离仓库无法继续；现将受管残留保存在原会话分支。
5. API readiness 的瞬时 502 导致环境 gate 误失败；现仅对 readiness 做 3 次短重试。
6. trial 写出受管目录外的未跟踪文件未被当场识别；现于当前 trial 完成阶段立即标记为
   `forbidden_file_change`，避免污染后续样本。

## 限制

- 策略实验只有一次重复，任务集为 30 个合成且可确定评分的任务，不能外推到所有真实任务。
- `complex_008`–`complex_010` 没有有效策略结果；复杂层只有 7 组可评估配对。
- MiniMax 在正式运行中出现两次 `model_unavailable`，供应商可用性影响 observed success rate。
- provider/runtime 未提供可信 token usage，因此不能报告 token 成本。
- 可靠性实验的延迟数字仅代表本次 WSL2、8 logical CPU 环境和对应 timeout/heartbeat 配置。

## 简历可用表述

- 基于 Temporal 构建 Durable Agent，通过 50 次 Workflow Worker 与 20 次 Activity Worker
  SIGKILL 故障注入验证恢复与副作用安全；70/70 达到预定义安全结果，未观察到额外重复外部副作用。
- 设计 Transactional Outbox 恢复链路，在 Worker 停机期间积压 30 个请求后实现 30/30 最终处理，
  丢失 Run 与重复 Workflow 均为 0，积压清空耗时约 11.9 秒。
- 对非幂等 ack gap 采用持久化 operation intent、重试检测与 `uncertain` fail-closed；10/10 次
  阻止二次外部写入，不将该结果表述为通用 exactly-once。
- 在 27 组有效 Agent 配对任务中，ReAct 简单任务成功率 90%，Plan-and-Execute 中等任务成功率
  70%、较 ReAct 高 30 个百分点；据此实现按复杂度选择策略的可解释依据。
