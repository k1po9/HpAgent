# Benchmark Architecture

实验脚本按故障模型分区，输出写入同名的 `artifacts/benchmarks/<experiment>/` 子目录。

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
