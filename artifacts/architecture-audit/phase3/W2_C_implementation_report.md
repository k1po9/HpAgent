# Phase 3 W2-C — QQ Delivery

## 基线与范围

基线 `9be9e1c5b6f8eeb7f41f32feadccf4d0db66547f`（W2-B）。依照冻结 Phase 2.2 R2.1 的 Delivery 合同实施，不修改冻结决策、不进入 W3。

## 实现

`Agent execution → PG committed assistant result + delivery record → QQDeliveryService → QQDeliveryAdapter → protocol channel`。

- migration 036 新增 `qq_deliveries`。Chat completion 在同一 PG 事务提交 Message、output file 关联、Run completed 和唯一 Run delivery，冻结正文、来源和附件引用。重复 completion 不覆盖结果或新增 delivery。
- Delivery 有自己的 pending/sending/delivered/uncertain 状态、分段进度、attempts、可用时间与短 lease token。它不更新 Run execution state，不调用 Agent，不创建 start_run。
- 消费者用 PG `FOR UPDATE SKIP LOCKED` 领取一段，事务结束后才发送。成功推进段号，明确失败延迟 10 秒重试原段。发送限时 60 秒，delivery lease 120 秒；无 Agent lease 或 workspace 锁。
- 超时/异常及过期 sending 标记 uncertain。不能确定是否已送达时不伪记成功；内部 `retry_uncertain(account_id, run_id)` 允许运维明确接受重复风险后重发原段。状态/token CAS 拒绝旧发送者晚到更新。该内部恢复入口不新增用户 API/UI。
- QQ adapter 独立处理分段、NapCat quote/@、Official msg_id/msg_seq 和受保护附件引用。模型输出中的 CQ 字符转义；不让结果正文伪造 CQ 操作。
- NapCat delivery 根据 bot self_id 选择一个连接，使用 OneBot echo 回执确认成功，不广播给其他 bot。socket write 不等于成功。Official QQ 不再在请求成功前登记去重，delivery 重试不走基于正文前缀的内存去重；网络异常不伪装明确未发送。
- worker 启动独立 delivery consumer，并在关闭 channels/resources 前取消它。Web SSE 继续消费相同 PG Message 业务结果，未复用 QQ presentation。

## 验证

完整复现：`.venv/bin/python scripts/verify_w2c_contracts.py`。

**完整回归：140 passed，260.94 秒，无 skip**，见 [完整回归输出](W2_C_full_validation.txt)。包含 W2-B 的真实 PG/Temporal 生命周期、Web/QQ commands、Session、memory、resource、协议回归，以及 delivery 的失败重试、分段恢复、uncertain/显式恢复。

专项：

- [PG/Temporal 输出](W2_C_validation.txt)：10 passed。QQ → ingress → PG → Outbox → canonical durable → committed result → 首次失败 → delivery 重试，正文相同且模型调用次数不增加；同时回归 Web completed/failed/cancelled 与 History replay。
- [worker/协议输出](W2_C_unit_validation.txt)：16 passed。
- [adapter 输出](W2_C_adapter_validation.txt)：3 passed，覆盖 quote/@/CQ 转义/附件引用、NapCat 按 bot 选连接并等待确认、Official 相同内容失败后再次实际发送。

专项与完整回归重叠，不累加为独立测试数。使用空 schema 的隔离 PG、Redis 与 Temporal namespace；模型、工具及 QQ 外网为受控替身。初次测试发现导入路径及 worker 接线缩进错误，已修复后重新运行，上述文件为修复后证据。

## Gate 与限制

**W2-C 子范围：PASS。** 执行/投递分离和真实 PG/Temporal 双入口 delivery 场景通过；完整 W2 Gate 仍需整包归档，不凭本提交开始 W3。修改的 Python 文件通过 Ruff F 检查，`git diff --check` 通过。

- 没有宣称真实 QQ 外网或任意渠道端 exactly-once；确认丢失的 uncertain 重试可能重复一段。
- 附件输出为受保护文件引用，需原账户通过 Web 下载，不新增公开链接、原生 QQ 上传或 File 生命周期。没有共用 Web SSE presentation。
- 本次投递 completed assistant result；失败/取消 Run 的最终状态通知未扩展为 delivery payload，取消控制提示仍沿用 W2-B。
- 未实现新 UI、Research UniversalWorkflow、W5/W6；历史 Agent/Workflow 删除留待 W3。

## Architecture deviation

无。delivery 表作为独立事件/状态存储、保留不确定性、附件引用适配均在冻结合同内。正式实现文档已同步，旧 Excalidraw 图继续标记 visual drift。

用户已有 `phase2_1/03_architecture_truth_table.md` 修改不纳入提交。
