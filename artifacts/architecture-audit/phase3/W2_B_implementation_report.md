# Phase 3 W2-B — QQ Ingress → Canonical Command

## 范围与基线

基线：`64f54abda3d5cf64d380d2e55da927e468e1ab62`（W2-A）。冻结合同为 Phase 2.2 R2.1；未修改合同。此次仅完成 W2-B，不代表完整 W2 退出，也不进入 W3。

## 已实现

- NapCat / Official QQ 协议规范化保留真实外部消息 ID、bot、scope、room、thread 与 sender。缺失真实 ID 的事件不使用随机 UUID 兜底。
- `ConversationService` 调用 surface-neutral `SurfaceConversationCommands`，在 PG 内验证 active/verified identity 与 active account，不自动创建账号。身份读取是此次授权判定点；撤销后重新投递需重新验证。
- binding 以 account + channel/bot/scope/room/thread 寻址。private 按 sender，group 按群，guild 按 guild/channel，dm 按 guild/sender；不同账户不共享 Conversation。
- migration 035 增加 Conversation bindings、不可变 ingress receipts 与 Message origin。一个事务完成 binding、共享 admission、Message/Session/Run/Outbox、来源及回执。独立首次消息通过 PG 唯一约束与 Conversation 行锁竞争；同消息使用短事务 advisory lock 串行去重。
- 回执不含 pending/claim/dispatch 状态，不是 execution queue。相同来源 ID 重投返回已存 outcome，内容冲突拒绝；busy 重投不会在后来空闲时偷偷启动 Run。删除短期 command 幂等记录后，ingress 回执仍有效。
- Session 使用 PG active Session，终态后复用；显式轮换才生成后继，活跃 Run 时轮换被共享 policy 拒绝。
- `/cancel [run UUID]` 限定当前 account/Conversation，使用共享取消与 Outbox 路径；重投回执不重新选择后来 Run。控制命令不生成 Agent Run 或 Chat Message。
- 非触发群消息仅更新可选 ambient cache；缓存故障不会使非触发消息执行。触发时上下文及来源 ID 固化到 PG Message origin。执行与 memory retention 从 PG 读取；群长期记忆召回按来源 context key 隔离。
- 生产 QQ composition 不再构造 Host/Facade/loop/SessionStore，旧 QQ Workflow/Activity 不再注册。QQ channel 启用时组装同一 canonical durable runtime；预算配置与 Web 使用同一环境设置。
- 上下文 profile、Run surface、workspace 路由与 retain 来源支持 QQ；Trace sink 不再伪写 Web 来源。历史实现删除留待 W3。

## 验证

复现：`.venv/bin/python scripts/verify_w2b_contracts.py`

最终结果：**136 passed，458.46 秒，无 skip**。原始输出见 [W2_B_validation.txt](W2_B_validation.txt)。隔离 PostgreSQL 从空 schema 执行 migrations，独立 Redis 容器及 Temporal namespace；外部模型/工具/QQ 网络使用测试替身。

覆盖协议 private/group/guild/dm、稳定消息键、并发重投及不同首次消息、共享 busy、Session 复用/轮换、取消回执、身份未绑定/撤销、DB 故障与整笔事务回滚、非触发/缓存故障、PG 上下文与记忆来源。真实 PG/Temporal QQ 入站用例覆盖 completed/cancelled、Outbox dispatch、丢失启动确认、重投与 History replay；同时回归 Web canonical 生命周期、资源恢复、Trace、Memory 与共享 commands。

修改的 Python 文件通过 Ruff F 检查；`git diff --check` 通过。初轮 PG 7、unit 77、runtime 51 通过，最终 136 项已包含这些覆盖及新增并发用例，不能将重复运行累加为独立测试数。

## Gate

| 范围 | 结果 |
| --- | --- |
| W2-B 入站规范化、身份/绑定、共享 PG command 与 admission | PASS |
| W2-B Run + Outbox → canonical durable 完成/取消（测试环境） | PASS |
| 所选 Web / Session / Memory / resource 回归 | PASS |
| 完整 W2 / G06 双入口端到端投递 | NOT COMPLETE |
| W3 retirement | 未开始，不允许凭本提交进入 |

## 明确未完成

QQ 最终回答尚无可靠 delivery consumer：当前入站可以创建并完成 canonical Run，但不会自动把已提交最终回答可靠投递回 QQ。控制提示仅作 best-effort 回复，失败不重新执行 command。真实机器人网络的引用、@、附件投递、失败恢复，以及完整 QQ → PG → durable → delivery 验收须在后续 W2 完成。此提交是中间实现节点，不是完整 QQ 产品上线验收。

媒体引用仅保存到 origin，不新增 File/Document/Workspace 生命周期。未实现新 UI、Research UniversalWorkflow 或 W5/W6。完整 W2 Gate 通过并提交之后才能进入 W3。

## Architecture deviation

无。本次事务封装、binding/key 格式与控制命令回执属于实现细节，没有改变冻结 authority 或 runtime 决策。当前架构说明已更新；旧组件图和清单已明确标记历史，Excalidraw visual drift 待完整 W2 后人工同步。

原有 `phase2_1/03_architecture_truth_table.md` 工作区修改未纳入本次提交。
