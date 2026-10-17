# HpAgent Benchmark Evidence

该目录按实验边界组织设计、结果和原始证据。根目录只负责导航，避免把不同故障模型、统计
口径和试运行数据混在一起。

## 目录

```text
artifacts/benchmarks/
├── summary/                         # 跨实验结论与简历表述
├── temporal/
│   ├── README.md                    # Temporal 总设计与两类 Worker 结论
│   ├── workflow_worker/             # 50 次 Workflow Worker SIGKILL
│   │   ├── report.md
│   │   ├── summary.json
│   │   ├── trials.csv
│   │   └── runs/                    # 边界标记与逐次 SQLite 状态
│   └── activity_worker/             # 20 次 Activity Worker SIGKILL/ack gap
│       ├── report.md
│       ├── summary.json
│       ├── trials.csv
│       └── runs/
├── outbox/                          # 30 请求宕机恢复
│   ├── README.md
│   ├── report.md
│   ├── summary.json
│   └── trials.csv
└── agent_strategy/                  # ReAct vs Plan-and-Execute
    ├── README.md
    ├── report.md
    ├── summary.json
    ├── trials.csv
    ├── trials.jsonl
    └── pilots/                      # gate、延迟试跑与问题记录
```

## 阅读顺序

1. [`summary/FINAL_ANALYSIS.md`](summary/FINAL_ANALYSIS.md)：完整跨实验分析和有效性边界。
2. [`summary/FINAL_SUMMARY.json`](summary/FINAL_SUMMARY.json)：机器可读核心指标。
3. 各实验 `README.md`：实验目标、设计、指标定义、结果和局限。
4. 各实验 `report.md`、`summary.json`、`trials.csv`：生成报告、聚合数据和逐条数据。

原始失败和环境错误均保留并明确标注，没有为了提高数字而删除或改写样本。

## 原始材料归档（2026-10-17）

上方目录树描述实验时布局。`temporal/*/runs/` 的 SQLite、同步标记及原始日志完整移至受限外部归档，包含失败与环境错误；未筛选样本。实验设计、报告、汇总、逐条 CSV/JSONL 及 pilot 记录继续保留，字节未改写。按原路径恢复的方法见[归档索引](../../docs/development/repository-archive-index.md)。
