# Phase 3 · W1-C Web Canonical Cutover

实施基线：`939f39febcb32f35ad9e68432cd2f2b296f295c2`（W1-B），前置 W1-A 为 `c0e718a`。
架构合同：Phase 2.2 R2.1 ACD-01/04、G03/G04。此次只执行 W1-C，不进入 W2/W3，不改写冻结的 phase2_2 或此前实施报告。

## 实现

Web Command → PG Run + Outbox → Dispatcher → `AgentLifecycleWorkflow` → `AgentRunWorkflow` → ReAct / Plan → segmented Activities。

- 将原 `DurableWebRunWorkflow` 演进为唯一 `AgentLifecycleWorkflow`，没有新增并存 runtime。生命周期只接收 `RunLifecycleInput(schema_version, run_id)`，不固定 Conversation、Session、surface/profile 或 lease token。
- `run_lifecycle_contracts.py` 成为 queue、DTO、重试和终态合同的 owner；目标业务 Workflow、Document routing 等仅更新这些共享定义的 import，不改变各自生命周期。legacy Workflow 反向消费共享合同，源码暂留 W3。
- `run_lifecycle_activities.py` 通过 RunLifecyclePort、RunInputLoader 与事件工厂接入当前 Chat 适配器；不从 legacy Activity 模块加载执行 Host。`ChatRunInputLoader` 校验 PG chat shape，缺失的聊天 ID 不再变成字符串 `"None"`。`ChatExecutionBindings` 承接 Chat capability 的 Session/transcript 绑定。Temporal cancellation control 迁到中立模块。
- 当前 Web 的 PG context loader/profile、workspace recovery 和 SSE/Trace factory 均是 composition 提供的 Chat/surface 适配；终态 trace 不再硬编码 `surface=web`。未来非 Chat source 可提供自己的 loader/lifecycle/context bindings，当前只测试无 Chat 输入合同，不声称新增了 Scheduled/File 等产品入口或非 Chat transcript 存储实现。
- Dispatcher 无条件启动 canonical 类型，固定 Workflow ID + REJECT_DUPLICATE；AlreadyStarted 只接受 canonical 类型，拒绝 legacy/其他类型占有同一 ID。取消仍服从 PG authority；PG 已 cancelled 时，迟到的 Agent 成功／失败均保持 Workflow cancelled。
- 移除生产 Worker 的 legacy Web Host/Facade/loop 构造和 `execute_agent_activity` 注册；lifecycle registry 仅注册 canonical Agent lifecycle 及独立 Research/Schedule/Artifact/Document Workflows。Agent registry 保留唯一 AgentRun/ReAct/Plan/Step/Tool。
- 删除 durable runtime 分流字段、环境解析和 Compose/example 配置；API 始终公开 ReAct/Plan，文件 transform 只受自己的能力配置限制。保留 Web real-Agent 启用与资源拓扑的既有启动检查。
- 修复 Start 已被接收、Run 已 running 而执行记录仍待恢复时的 Dispatcher 重投缺口：由 `prepare_start` 决定恢复，不再用 `still_queued` 提前阻断；恢复只使用同一个 ID。
- 真实取消测试发现模型 Activity 缺少持续心跳，导致取消不能及时到达。分段 Activity 包装层新增周期心跳，所有 capability segment 默认 45s heartbeat timeout；取消等待 Activity 清理再 release，等待阶段没有该心跳 Activity。原工具副作用/uncertain/fencing 语义不变。
- Tool Workflow 地址派生移至纯 `agent_workflows.ids`，API/命令/审批不再为派生 ID 而加载 Temporal Workflow。这是切换所需的依赖切口，不是 File 领域重构。

## 验证方式与证据

统一命令：`.venv/bin/python scripts/verify_w1c_contracts.py`。
沿用 W1-B 隔离 runner，创建临时 PostgreSQL 容器、独立 API/Worker 角色和新的 Temporal namespace，从空 schema 执行 migrations。namespace 的 `w1b` 前缀仅为复用测试工具的命名，不表示旧运行时。

统一验收：**181 passed / 0 skipped，261.56s**，namespace
`hpagent-w1b-test-6a77663adb`。随后针对终态竞争修复运行
`test/test_w1c_cutover_contract.py test/test_agent_segment_replay.py`：**12 passed / 0 skipped，2.74s**，
包含新增的两个迟到成功／失败遇到 PG cancelled 的用例及五份离线 replay。
这两次运行有用例重叠，不累加为 193 个独立测试。Ruff、`git diff --check` 通过。
临时 PG 容器已移除；未清理或改写现有应用数据库。唯一 warning 为既有 Starlette/httpx 弃用提示。

关键覆盖：

1. 真实 Command + Outbox + Dispatcher + canonical lifecycle + 实际 durable Activities，覆盖 Web ReAct/Plan 的 complete/failure/cancel。只有模型、工具服务与 Sandbox 对外设施为受控测试替身；上下文组装、Git 工作区恢复、PG transcript/segment/终态提交真实执行。
2. Temporal 已接收 Start 但 Dispatcher 响应丢失；Outbox 重投和已关闭 Workflow 的重复 Start 不重复执行，不重复 Message；运行中缺失 Start 记录可恢复。
3. 完成/失败/取消的 PG Run authority、租约释放、Outbox processed；已 terminal/cancelling 的 Run 不启动 Agent child。错误类型占用同一 Workflow ID 被拒绝。
4. 同时 SIGKILL lifecycle 和 Agent Workflow 两个真实进程，在 ReAct tool / Plan step 的恢复边界重启；已完成 operation 与副作用不重复。原真实 Activity worker kill、lease expiry/reacquire/fencing、suspend/resume、审批、预算与 uncertain 回归继续运行。
5. 新 canonical lifecycle 完成 histories 单独捕获，连同 W1-B 的 ReAct/Plan/Tool 三份目标 histories 共五份离线 replay。PG E2E 和进程恢复测试还对实际 history 在线取回后 replay。
6. canonical registry、无分流开关、API import 隔离、Chat shape 校验，以及可注入非 Chat Run input 合同。

旧单 Activity Web Temporal integration 测试改为目标终态合同，原业务场景由以上目标 PG E2E 与 worker kill 测试接替。legacy Workflow 源码、旧离线 fixture 和 replay 测试仍留给 W3，不将新 History 改写成旧 fixture 冒充兼容。

更新 History capture 脚本及 Outbox benchmark worker 到 canonical 调用合同。benchmark worker 的 CLI/import smoke 通过，未执行整套性能 benchmark，不作性能结论。README、当前架构与部署说明同步移除分流描述；历史 architecture-audit 保持原文。

## Gate 与后续范围

**W1-C Gate：通过（VERIFIED）。**
W1-A/B/C 的本次 Web 范围按 G03/G04 验证；不把 W2 的双入口 G06 或 W3 的退役 G05 标为完成。本次 Session 到 W1-C 提交为止。

没有阻塞本工作包的 ARCHITECTURE DEVIATION。普通接线与取消修复遵循冻结合同，没有改变 Canonical Architecture。

下一工作包仍需：W2 将 QQ 接入统一 Conversation/PG Run/Outbox 和投递；W3 删除 legacy Web/QQ Workflow、Host/loop、旧状态权威及剩余仅为 legacy 服务的合同与测试。完整组合整理、ModelInputSnapshot/Review 和 Workspace 查询分别留给后续工作包。

用户原有 `phase2_1/03_architecture_truth_table.md` 修改不纳入提交。提交 hash 可通过 `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W1_C_implementation_report.md` 定位，最终答复给出实际 hash。
