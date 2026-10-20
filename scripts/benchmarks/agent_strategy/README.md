# Agent 策略实验

比较 ReAct / Plan-and-Execute；任务输入见 [agent_strategy_tasks.yaml](agent_strategy_tasks.yaml)，驱动见 [agent_strategy_experiment.py](agent_strategy_experiment.py)，运行和统计见 [runner](agent_strategy_benchmark.py)、[summarizer](summarize_agent_strategy_benchmark.py)。

根目录入口：

```bash
make agent-benchmark-check
make agent-benchmark-run
```

check 会探测真实服务与 Provider 连通性；run 会再次检查并执行 pilot，满足 gate 后运行平衡实验，会请求模型、写账户/工作区/业务记录并产生费用。必须使用专用 benchmark 账户、PG、Temporal 和代码工作区，核对 Worker 工具与实际模型配置。参数、依赖和失败行为见[脚本入口](../../README.md)，不能把环境探测当作产品验证。

离线输入预检（不请求模型）：

```bash
PYTHONPATH=src python3 scripts/benchmarks/agent_strategy/agent_strategy_benchmark.py preflight
```

默认输出在忽略的 `artifacts/benchmark-runs/agent_strategy/`，可用 BENCHMARK_OUTPUT_DIR 指定独立批次。保留原始失败、环境错误、pilot 和正式样本，保持相同模型、工具、温度、预算与任务集；不同配置使用独立输出，不混入统计。汇总只对完整平衡设计作正式结论，部分结果使用 --allow-partial 并标明限制。

脚本包含对工作区/Git 与运行时记录的特定假设；执行前核对当前服务接口。当前产品的账本能力不等于实验脚本已采集同样字段；缺失 token 或耗时保持缺失，不能推算。验证结果属于该次环境，不写入源码文档作为长期事实。
