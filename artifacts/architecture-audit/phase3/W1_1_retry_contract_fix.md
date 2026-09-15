# Phase 3 · W1.1 Retry Contract Audit Fix

Baseline：`1c6a86e280f1acae5069bdc80a917663207ff069`。

## Problem

本地 Temporal SDK `RetryPolicy.maximum_attempts: int = 0` 表示 unlimited/no maximum。
`execute_segment()` 曾通过 `retry.maximum_attempts or 3` 将 0 静默转换为 3，与 SDK 合同不一致。
这是 latent contract bug，未发现当前有限 production caller 因此失败。

## Fix

Capability segment retry 明确要求 bounded RetryPolicy：`maximum_attempts > 0` 执行对应的最大尝试次数，`<= 0` 或 `None` 在分配/acquire segment 前抛出 `ValueError("execute_segment requires a bounded retry policy")`。
未指定 policy 仍默认最多 3 次。每次 attempt 的 release → Workflow timer → 新 segment/token 顺序不变。

Control-plane `_control()` 的 `_CONTROL_RETRY(maximum_attempts=0)` 保持不变；capability 内单次 Activity 仍使用 `_SINGLE_ATTEMPT(maximum_attempts=1)`。

检查了全部 `execute_segment`、`RetryPolicy`、`maximum_attempts` 引用。11 处生产 capability 调用均显式有限：ReAct bootstrap 为 5；ReAct model/final、Plan planning/evaluation/replan/synthesis、AgentStep model/final、Tool/approved Tool 为 3。没有修改调用者 retry policy，也没有新增无限 capability retry。

## Scope

仅修改 `src/agent_workflows/segments.py`、`test/test_durable_agent_contract.py` 和本补充 evidence。
无 Architecture Decision 改变；无 W2/W3/W4/W5/W6 实现。Phase 2.2 frozen files 与原 W1 implementation report 均未修改。用户原有 phase2_1 修改未纳入提交。

## Tests

- `.venv/bin/python -m pytest -q test/test_durable_agent_contract.py --tb=short` → **19 passed / 0 failed / 0 skipped，3.28s**。新增 7 个参数化用例，验证有限 1/3、默认 3、拒绝 0/-1/None，以及 control retry 仍为 unlimited；有限重试同时核验 release/timer/acquire 顺序与新 segment/token。
- `.venv/bin/python scripts/verify_w1_gate.py` → **216 passed / 0 failed / 0 skipped，382.62s**。使用原 isolated PostgreSQL / Temporal fixtures；namespace `hpagent-w1b-test-89c3066744`。包含上述 contract cases，不重复累计。唯一 warning 为既有 Starlette/httpx 弃用提示。
- `.venv/bin/ruff check src/agent_workflows/segments.py test/test_durable_agent_contract.py` → **PASS**。
- `git diff --check` → **PASS**。

## Result

**CODE REVIEW PASS / FULL W1 GATE VERIFIED。W1 invariants unchanged。**

Source-neutral input 不携带永久 lease token；Run lifetime 与 segment lifetime 分离；重试释放旧 segment 后经 timer 获取新 token；durable wait 不持有执行租约/workspace lock/Activity slot；Web 仍经 Dispatcher → AgentLifecycleWorkflow → AgentRunWorkflow，production registry 没有重新注册 legacy Web runtime。

Remaining follow-ups：本次范围内无。W2 未开始。
