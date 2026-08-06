# Temporal History fixtures

本目录保存从真实 Temporal Server 捕获并冻结的 Workflow History，作为历史兼容性
基线（D-09）。不允许普通测试或 CI 自动生成、刷新或覆盖这些文件。

## `web_run_workflow_v1_completed.json`

| 项目 | 内容 |
|---|---|
| 文件名 | `web_run_workflow_v1_completed.json` |
| Workflow type | `WebRunWorkflow` |
| Workflow schema version | `1` |
| 原始 workflow_id | `hpagent-web-run-a423e6b8-17b8-4446-8ed2-873152ae8329` |
| 捕获命令 | `PYTHONPATH=.:src python scripts/capture_web_workflow_history.py --host localhost:7233` |
| 捕获时的 Temporal Server 版本 | `1.26.2`（`temporalio/auto-setup:1.26.2`） |
| 捕获时的 Temporal Python SDK 版本 | `1.31.0` |
| 捕获所基于的 Git commit SHA | `e542ef5a2e079fa543d55a9b0ceade930f050f9c` |

捕获方式：`scripts/capture_web_workflow_history.py` 连接 `TEMPORAL_HOST`，注册
`WebRunWorkflow` 与最小 stub activities，启动一次完成的最小 happy-path Execution，
等待完成后用 Python SDK 的 `handle.fetch_history()` + `history.to_json()` 直接写
入本文件（不经 Web UI 或浏览器），随后立即从磁盘重新读取并离线 replay 验证。默认
拒绝覆盖已存在的 fixture，只有显式 `--force` 才允许重新捕获。

fixture 内容：完整 History（非仅 close event）。输入只含 `{"schema_version": 1,
"run_id": "..."}`；所有 payload 只含 `run_id`、`schema_version`、`status`、
`outcome`，不含凭证、API Key、用户消息、Prompt、模型回复或本地路径。工作流已正常
关闭为 completed happy path，调度过 `prepare_run_activity` 与
`execute_agent_activity`。

用途：离线 replay 门禁。`test/test_web_temporal_replay.py` 的
`test_web_run_workflow_replays_frozen_v1_completed_history` 在不连接 Temporal、
PostgreSQL、不启动 Docker 的情况下，用当前 `WebRunWorkflow` 代码 replay 本文件，
检测未来 Workflow 修改是否破坏历史确定性。

## 更新规则

> 该 fixture 是历史兼容性基线。Workflow 修改导致 replay 失败时，不得直接刷新
> fixture 来让测试通过。必须先判断修改是否破坏确定性；非兼容修改应采用 Temporal
> patch/version 机制并保留旧分支。只有明确建立新的兼容性基线时，才允许增加新
> fixture；原则上保留旧 fixture，而不是覆盖旧 fixture。

对应地，CI 中的冻结 History replay 步骤从不刷新 fixture；`scripts/`
`capture_web_workflow_history.py` 只在显式调用且（默认）目标文件不存在时才会写入。
