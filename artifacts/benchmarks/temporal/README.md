# Temporal Durable Agent 故障恢复实验

## 实验目标

验证 HpAgent 在真实 Worker 进程丢失后能否依靠 Temporal Event History 或 Activity retry
恢复，并验证外部副作用已发生但 completion ack 丢失时的安全边界。

## 子实验设计

### 1. Workflow Worker recovery

每次启动独立 Activity Worker 和 Workflow Worker，在指定边界发送 SIGKILL，随后启动 replacement
Workflow Worker 并等待同一 Workflow。五类边界各 10 次：

| Case | 策略 | 故障边界 | 次数 |
|---|---|---|---:|
| T1 | ReAct | model decision | 10 |
| T2 | ReAct | tool side effect 前 | 10 |
| T3 | ReAct | side effect 后、Activity result 释放前 | 10 |
| T4 | Plan-and-Execute | plan step 2 | 10 |
| T5 | Plan-and-Execute | final evaluation | 10 |

指标为 recovery success rate、duplicate operation/side-effect trial rate，以及从 kill 到恢复的
P50/P95 延迟。详细设计和逐边界结果见 [`workflow_worker/report.md`](workflow_worker/report.md)。

### 2. Activity Worker recovery 补充实验

Workflow Worker 保持运行，SIGKILL 独立 Activity Worker，再启动 replacement Activity Worker。

| Case | 故障边界 | 语义 | 次数 |
|---|---|---|---:|
| A1 | side effect 前 | 尚未发生外部效果 | 5 |
| A2 | 幂等写成功、ack 前 | 允许安全重试 | 5 |
| A3 | 非幂等写成功、ack 前 | 无法确认时 fail closed | 10 |

指标为 safe outcome rate、Activity retry observed rate、A3 unsafe duplicate side-effect rate 和
kill-to-terminal P50/P95。详细设计见 [`activity_worker/report.md`](activity_worker/report.md)。

## 结果

- Workflow Worker：50/50 恢复成功；重复 operation trial 0，重复副作用 trial 0；P50
  10,485.010 ms，P95 10,683.786 ms。
- Activity Worker：20/20 观察到 retry 且达到安全结果；A1/A2 10/10 最终完成；A3 10/10
  进入 `uncertain` fail-closed，额外重复外部副作用 0/10；P50 3,998.718 ms，P95
  4,129.283 ms。

## 解释边界

Workflow 结果证明 replay-safe recovery；A3 证明系统在无法确认非幂等调用结果时阻止第二次写入。
二者都不构成任意外部工具的通用 exactly-once 声明。

## 复现脚本

- `scripts/benchmarks/temporal/temporal_recovery_benchmark.py`
- `scripts/benchmarks/temporal/activity_worker_recovery_benchmark.py`
