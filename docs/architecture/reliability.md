# 可靠性

- **Transactional Outbox**：命令状态与分发意图同事务；start、cancel、terminal 与 Work event 分别发布，Dispatcher lease 可恢复。
- **Temporal Durability**：有限 Workflow 持久编排，非确定性 I/O 放在 Activity。数据库已提交业务事实决定完成，不以 Temporal completed 推断领域成功。
- **Idempotency**：Command Key、稳定 Workflow ID、operation、effect key 和回执避免重复副作用。意图相同但响应不确定时重用幂等键，不能每次点击换键。
- **Work coordination**：Run / requirement revision / control epoch 校验协调权；修订、暂停和停止阻断旧结果推进新状态。
- **Execution lease / fencing**：attempt 和 segment 持租约；新 token 拒绝旧写入。不是 Account 全执行独占锁，聊天与后台 Work 可独立 admission；资源争用另用锁/容量。
- **Result receipts**：operation attempt 注册生产者，迟到回执保留原来源；旧 attempt 不能追加当前 transcript、写 Workspace 或完成新 revision。
- **Budget / capacity**：Account/day、累计 Work 和 Run 原子预留；失败回滚避免孤立 hold。未知外部模型调用按估算承担费用，发送前取消释放。跨进程容量票据续租/过期恢复，交互保留和账户轮转限制后台负载。
- **Wait / continuation**：Approval 与 segment wait 持久化，恢复重新获执行权；Work ready 的持久 wakeup、once catch-up 与 daily latest 恢复不依赖进程内 timer。
- **Side-effect safety**：未知结果进行 reconciliation。投递 sending lease 失效转 uncertain，显式 resolution 后才决定后续；停止有不确定外部写入时保留 blocker。
- **Authorization**：Run 固定候选而非永久授权；select / read / save 检查当前 grant、Work revision 与 fence，撤权阻断后续受控访问。
- **Cancellation**：取消 Run 与暂停/停止 Work 分开；Lifecycle、Child、Activity 与数据库终态收敛。attempt scratch 隔离，旧清理不删除新尝试目录。
- **Delivery separation**：通知引用已提交 Message 或 Work 事件/operation，渠道投递可独立失败；accepted 不代表已读或用户验收。
- **Process ownership**：先停止 ingress / 后台 producer，再退出 Temporal Worker，最后逆序关闭共享资源；健康探针核验 schema、Temporal、两个 Worker 和 Dispatcher。

## 模型失败与重试

Provider 请求格式在快照和预算预留前校验。`model_request_invalid / model_response_invalid / model_access_denied / model_request_rejected / model_rate_limited` 当前不可自动重试；`model_connection_failed / model_read_timeout / model_http_error / model_unavailable` 当前可重试。分类代码贯通 Activity、Lifecycle、Run DTO 和重试命令。普通模型故障不能标记为“外部副作用不确定”。HTTP 429 当前作为不可重试额度/限流拒绝，调整策略需另行修改契约。

独立 Artifact 读取预算默认 90 秒，连接等待最多 5 秒；不能通过延长所有聊天超时掩盖请求格式或权限问题。日志与诊断见[故障排查](../operations/troubleshooting.md)。

## 已知恢复验收限制

2026-10-03 的 Research SIGKILL 回归使用 5 秒自然到期 stage lease 验证接管和成果去重；生产默认 stage lease 3600 秒与 publish/save Activity 的 120 秒 schedule-to-close 不匹配，尚未证明默认配置下快速恢复。2026-10-04 的修复未修改此策略。

受控恢复、浏览器和真实供应商验证分别记录，见[实施索引](../implementation/README.md)。当前 MiniMax-M3 工具往返复验在连接阶段超时，不能用测试执行器的成功替代真实 Provider 验收。
