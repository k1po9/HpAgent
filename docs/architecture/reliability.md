# 可靠性

- **Transactional Outbox**：命令状态和分发意图一起提交；Dispatcher Lease 支持进程中断后的恢复。
- **Temporal Durability**：Workflow 在 Worker 重启后保持生命周期和策略进度；非确定性操作隔离在 Activity 中。
- **Activity Retry**：可重试故障使用 Temporal Policy；永久校验和契约错误标记为不可重试。
- **Idempotency**：Command Key、Workflow ID、Operation ID、Provider Key 和持久化结果避免重复业务副作用。
- **Lease 与 Fencing**：Account Execution Lease 串行化冲突 Run；单调递增的 Fencing Token 拒绝 Lease 重获后的陈旧写入。
- **Durable Wait/Resume**：Approval 与等待状态被持久化；恢复的 Segment 必须重新获得执行权。
- **Side-effect Safety**：Operation Ledger 记录意图与完成状态；Activity 对不确定外部结果进行 Reconciliation，而不是盲目重做。
- **Cancellation**：取消依次传递到 Lifecycle Workflow、Child Workflow、Activity 和 PostgreSQL 终态；清理会释放资源并阻止陈旧 Worker 提交。
- **Delivery Separation**：QQ Delivery 重试只消费已提交输出，不会重新执行 Agent；投递失败与执行失败彼此独立。
- **Process Ownership**：Worker 先停止 Ingress 和后台 Producer，再退出 Temporal Worker，最后按资源获取的逆序关闭共享基础设施。
