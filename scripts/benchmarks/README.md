# Benchmark Architecture

实验脚本按故障模型分区，代码和输入保留在本目录。运行输出默认写入忽略的 `artifacts/benchmark-runs/<experiment>/`，不提交结果或阶段报告。Temporal/Outbox 可用 `--output-dir` 覆盖；Agent 策略用 `BENCHMARK_OUTPUT_DIR` 覆盖。所有失败与环境错误样本均保留，不改变统计口径。脚本的实际依赖、输出及副作用见[公共入口](../README.md)；实验不作为产品门禁。

```text
scripts/benchmarks/
├── temporal/
│   ├── temporal_recovery_benchmark.py
│   └── activity_worker_recovery_benchmark.py
├── outbox/
│   ├── outbox_recovery_benchmark.py
│   └── outbox_benchmark_worker.py
└── agent_strategy/
    ├── README.md
    ├── agent_strategy.sh
    ├── agent_strategy_experiment.py
    ├── agent_strategy_benchmark.py
    ├── summarize_agent_strategy_benchmark.py
    └── agent_strategy_tasks.yaml
```

Temporal 两个脚本共享现有 `test/support/durable_worker_process.py` 进程 harness；Outbox 的
replacement Worker 与实验脚本放在同一目录；Agent 策略包同时包含 gate、runner、任务集和
汇总器。测试脚本位于 `test/benchmarks/`。
