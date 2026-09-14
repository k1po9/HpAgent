# 实施顺序与门禁 · R2

> Historical architecture evidence. Not current architecture documentation.
> TARGET DECISION / NOT IMPLEMENTED。本轮未执行下表工作包。R1 门禁定义已被替代，尤其 G05 不再是线上 History 退役门禁，G06 改为双入口领域验证。

## 工作包与依赖

| 包 | 依赖 / ACD | 最小交付与退出条件 |
| --- | --- | --- |
| W0 目标冻结 | 无；01/04/06/14/17/18 | ADR 写目标与未实现状态；当前架构文档只写 HEAD。冻结统一 identity/conversation/run/delivery 与快照/query 扩展合同；正式文档/ADR 实际修改留到后续实施 |
| W1 唯一 durable 主线 | W0；01/08 | 在现有 durable 上抽出中立 lifecycle/input/loader/events，Web 先验证 ReAct/Plan、终态/取消/恢复；已有 QQ 临时尚在旧链，不可称双入口目标完成 |
| W2 Conversation/QQ 收敛 | W1；01/04/07 | QQ 改调 PG 命令+Outbox，统一消息幂等/Session/busy/身份，投递与执行分开；两个 surface 同一主线验证 G06。按需救出 DTO/错误等目标依赖 |
| W3 删除非目标实现 | W2；03/08/09/10 | 按 08 的对象级清单迁出共享依赖，然后删 legacy loop/Workflow/Activity、QQ WAL 权威、双分派/开关、实验/旧账号兼容；目标 tests 与 clean-install 通过，无永久旧路径 |
| W4 composition/protocol 清理 | W3；02/03 | 唯一 runtime 装配，surface 只构造适配器；明确所有共享资源拥有权与关闭；移除临时 wrapper |
| W5 Model Input 边界 | W4；12/17 | Prepare/Invoke、不可变 snapshot、provider body 冻结、review policy、等待/失效/查询投影合同。可先 review disabled 跑同一调用链，再补完整审批；review 功能开启前 G12 必须完成 |
| W6 Workspace Query | W4；16/18 | workspace binding、tree/file/status/diff/metadata、committed/working 与版本合同；shared-checkout 下可先提供真实可用范围，不要求先做 worktree |
| W7 能力结构整理 | 相关 W5/W6 合同稳定；05/11/13 | 持久文件规则/执行拆分，MCP transport/projection，依赖维护源；保留 Document/Research/Artifact 语义，验证全新环境安装 |

建议排期先 W5 再 W6；依赖图允许 W4 后分别实施，它们不存在人为串行硬依赖。Prompt Review UI、Workspace UI、Research UI 各自后续交付；完整 session worktree 为 FUTURE OPTION，不是 canonical 架构验收前置。

W3 删除依赖 ACD-03 的“救出正式协议”，W4 才做完整协议整理；这是必要的小切口前移，不是循环依赖。W1/W2 的短暂中间状态只存在于未完成工作包，不能成为正式发布形态；W3 退出须删除过渡开关。

## 门禁

已有测试仅是可复用场景入口，本轮只读未执行。后续应为目标修改或替换旧合同断言，新增缺失行为测试；跳过依赖真实 PG/Temporal 的用例不能记成通过。

| Gate | 证明与通过条件 | 当前证据 / 后续测试入口 |
| --- | --- | --- |
| G01 审计与文档 | ACD/证据/追踪/工作包/机器表一致；明确 Current/Target/Future/未实现，旧线上门禁不再混入当前建议；范围外字节与用户原修改保持 | audit_tools/validate_review.py；后续 ADR/HEAD docs 分开审查 |
| G02 组合与资源 | canonical 主线所有 caller 绑定正确；单份 pool/locks/Sandbox；启停、取消、启动失败释放资源；消费者 event_types/lease 不串用 | test/test_web_outbox_recovery.py、test/test_workspace_isolation.py；补目标构造图/异常关闭断言 |
| G03 目标合同 | 两 surface DTO/权限/错误/幂等、Workflow/Activity registration、操作 ID、配置与 schema 和选定目标一致；允许修改旧名称/接口，需更新消费者，不能为过旧测试保留实现 | test/test_durable_agent_contract.py、test/test_action_result_semantics.py、test/web_api/test_config.py；新增 canonical registry、无 legacy flag/导出断言 |
| G04 Durable 行为 | ReAct/Plan/step/replan/final、完成/失败/取消、worker kill、审批恢复、lease/fencing、预算、工具 uncertain、Outbox Start 重投均达预期；目标 History deterministic replay，通过 PG/Temporal 故障场景 | test/test_durable_agent_temporal_integration.py、test/test_durable_agent_worker_kill.py、test/web_persistence/test_durable_agent_activity_worker_kill.py、test/test_tool_execution_approval_temporal.py；建立目标 replay fixture |
| G05 主动退役 | 新 canonical 双入口路径工作；旧实现没有目标 caller/registry/配置/脚本用途；共享合同已迁出；目标行为测试、构建和全新 schema 通过；正式架构每类状态/Agent 编排只有一个权威 | 按 08 deletion candidates 逐项证据；不要求旧线上 History、生产数据回填、部署兼容或观察周期 |
| G06 双入口领域 | PG 同一命令与 Session/Run 规则；QQ 重投只接受一次；相同身份不串 Conversation；群聊/私聊授权、busy、轮换、取消/retry、记忆来源正确；投递重试不重执行 Agent，未绑定与 DB 失败区别明确 | test/test_qq_execution_compat.py 的业务场景迁入新测试；test/web_persistence/test_phase_c_sessions.py、test/web_api/test_sse_gateway.py；必须补 QQ → PG → Outbox → durable → delivery E2E |
| G07 worktree 演进 | 只有未来开放时才要求独立 worktree/分支/owner 正确，同/跨账户并发、跨进程锁、崩溃与未提交文件保护通过 | test/test_workspace_isolation.py、test/test_workspace_provisioning.py；当前 enum/validator 通过不代表能力完成；当前可以先拒绝假能力 |
| G08 产品查询面 | 未来 review/workspace UI 使用正式 query/projection/approval 合同；QQ review 控制使用同一授权对象。Research 专用 UI 另有范围，不阻塞 backend 固定 Workflow | web/src/App.tsx、web/src/components/ApprovalCard.tsx 仅现状入口；补权限投影和失效交互验收 |
| G09 MCP | 改动涉及的 HTTP/SSE/stdio connect/list/call/disconnect/取消/重连/keepalive、工具 schema/name/metadata 与结果语义正确；快照冻结工具版本 | test/test_durable_agent_hardening.py、test/test_mcp_health_script.py；补可控 transport 契约，不需穷尽世界上的远端工具 |
| G10 File / Research / Artifact | PG 事务中的审批+Outbox、revision CAS、幂等/lineage/账户归属，FS 已写而 PG 失败的恢复，Research report/version 原子关联不变；Document queue/读取范围独立 | test/web_persistence/test_persistent_web_files.py、test/web_persistence/test_file_action_approvals.py、test/web_persistence/test_file_lineage.py、test/web_persistence/test_research_r0_r2.py、test/test_document_worker.py |
| G11 全新安装 | 空开发 DB/目标 schema、全新镜像启动、queue registry 和所需服务可用；requirements 维护源一致；必要 migrations/SQL 函数/权限仍在 | 四份 src/Dockerfile* 与 src/persistence/migrate.py；实际构建/启动后记录，不以文本 COPY 检查替代 |
| G12 Model Review | 06 的不变量：无授权零主模型调用；所有主 Agent phase 都覆盖；approve/reject/expiry/cancel、wait 重启、重复/伪造/错 hash/跨 owner/旧 snapshot 信号、输入改变/模型 fallback 重审；权限视图同 canonical hash；发送 body 与批准快照一致；lease 过期、重获 token、CAS 与预算通过 | 当前 model_decision/planning/evaluate/ModelClient 仅为重构入口；文件 approval 测试可借场景但不是已覆盖。必须新增 snapshot/payload 与模型假服务断言 |
| G13 Workspace Query | 07 的 owner/逻辑路径/版本隔离；遍历/符号链接边界、二进制/大文件/分页、并发切分支/dirty snapshot、旧 Session 不误读当前 checkout；GET 不执行 checkout/provision/write；不同 file scope 不混淆 | test/test_workspace_isolation.py、test/test_run_file_workspace.py、test/test_file_workspace_security.py；新增 query/API 合同与无写副作用测试 |

## 旧测试与代码删除同步

冻结旧 WebRunWorkflow completed replay 和“双注册永远存在”断言属于旧合同。当目标路径验证通过后，允许移除这些测试/fixture，建立只覆盖新合同的 replay/regression 基线；不能直接改旧 fixture 伪称兼容。QQ 旧测试中身份拒绝、群聊路由、回复及记忆语义要迁入新行为测试，只有旧 Host 接线断言随实现删除。

“不需要旧线上兼容”不等于无需目标 Temporal replay、重试幂等或工具副作用保护。G05 也不要求 UNKNOWN 清零；它只要求每个拟删对象的目标调用/注册/数据/配置依赖有结论。Git 是源码回退路径；本轮及后续代码回退均不自动授权删除个人本地资产。

## 决策与实施状态

用户已明确 canonical 产品方向，具体 DTO/命名/busy 策略/工作包拆分为本审计建议。逐项采纳设计细节时记录日期、责任人和偏差理由；IMPLEMENTED/VERIFIED 只能附实际提交与 Gate 结果。本次只交付修订决策，所有目标改造为 NOT IMPLEMENTED。
