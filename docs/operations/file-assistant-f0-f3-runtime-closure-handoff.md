# File Assistant F0–F3 Runtime Closure 交接总结

更新时间：2026-09-04
分支：`feat/hpagent-web`
当前 HEAD：`800558e docs(file): map F0-F4 implementation drift`

> 本轮实现仍在工作树中，尚未 commit。新窗口接手后先运行 `git status --short`，不要丢弃或覆盖现有修改。

## 1. 当前状态

- **F0 Runtime Complete**
- **F1 Runtime Complete**
- **F2 Runtime Complete**
- **F3 Runtime Complete**
- **F4 Approval Foundation（前置准备已存在，本轮保持不变）**

F4 尚非 Runtime Complete：已有审批持久化与授权基础，但没有 Temporal 等待/恢复和真实 destructive action 的端到端闭环。

## 2. 本轮完成内容

### Binary Web Upload

- PDF、DOCX、XLSX、PPTX 可通过正式 Web Upload 进入 `stored_files`。
- attachment 经 `send_message` 绑定到 `run_files`，可由真实 `RunFileScope` 读取。
- binary 不执行 UTF-8 decode；`.log` 等文本继续执行严格 UTF-8 与 NUL 校验。
- 保留 streaming、size limit、SHA256、fsync、immutable storage 和原始 MIME。

代码与测试：`src/web_domain/file_services.py`、`src/storage/tenant_file_store.py`、
`src/persistence/repositories.py`、`test/web_api/test_binary_file_upload.py`。

### Idempotent File Write Crash Recovery

- `OutputPublisher.replay(...)` 在 adapter 写输出路径前按稳定 `(run_id, operation_id)` 查询已发布结果。
- 对“文件发布后、Activity ACK 前 Worker 退出”的 retry，返回原 `file_id/version` 和 `deduplicated=true`。
- 不重复生成、登记或扣预算；logical name/parent identity 不一致时拒绝复用。
- 同一 Run 的 SIGKILL 遗留 workspace 会从 immutable TenantFileStore 重建。

代码与测试：`src/file_runtime/output.py`、`src/sandbox/tools/local/file_write.py`、
`src/workspace/file_scope.py`、`test/web_persistence/test_file_output_persistence.py`、
`test/test_durable_agent_hardening.py`、`test/test_run_file_workspace.py`。

### PDF Conversion Lineage

- `convert_file_to_pdf` 发布时记录 `parent_file_id = source file_id`。
- retry replay 同时校验 parent identity。
- lineage API 测试确认 source → derived PDF 的 root-first 关系。

代码与测试：`src/sandbox/tools/local/file_write.py`、`test/web_persistence/test_file_lineage.py`。

### Large Document Auto Routing

- 新增 deterministic `TemporalDocumentRouter`，默认阈值 1 MiB，可由 `FILE_DIRECT_READ_MAX_BYTES` 配置。
- 小型文本继续 bounded direct read；超阈值或 PDF/DOCX/XLSX/PPTX 路由至现有
  `NormalizeDocumentWorkflow` / Document Worker。
- 使用稳定 workflow/operation ID；重复启动连接既有 Workflow。
- Temporal History 只返回 compact `NormalizedDocumentRef`，完整结果持久化到 `normalized_documents`。
- 未新增 Agent Framework、LLM Router，也未重构 Durable Agent。

代码与测试：`src/file_runtime/routing.py`、`src/sandbox/tools/local/file_read.py`、
`src/sandbox/sandbox_manager.py`、`src/orchestration/worker.py`、`.env.example`、
`docker-compose.yaml`、`test/test_file_read_tools.py`、`test/test_document_temporal_integration.py`。

## 3. F4 前置准备的真实边界

已完成的 Approval Foundation：

- `FileActionApproval` domain model 与 `file_action_approvals` 状态机。
- owned list、approve、reject API；CSRF、`Idempotency-Key` 和租户校验。
- grant 精确绑定 run、operation、tool 和 arguments SHA256。
- Worker 原子 consume 一次授权。
- migration 029 已应用，checksum 不得修改。

尚未完成：

- Workflow 暂停等待审批及批准/拒绝/超时/取消后的恢复。
- approval Outbox / Temporal Signal。
- 真实 external overwrite/delete/send adapter。
- 外部副作用前 grant consume、fencing 与 reconciliation 的全链路验收。
- Web approval inbox/按钮和 F4 Worker-kill 故障窗口验收。

准确表述：**F4 Approval Foundation 已完成；F4 Runtime 尚未完成。**

## 4. 验收证据

| 范围 | 结果 |
|---|---|
| Binary upload → send_message → RunFileScope；output persistence；lineage | 7 passed，1 warning |
| File/Document/Durable scoped unit | 43 passed |
| Document real Temporal integration | 2 passed |
| Agent/Research Temporal contract regression | 62 passed |
| 全量非 external、非 PostgreSQL | 511 passed，17 skipped，172 deselected |
| Ruff（本轮文件）/ compileall / diff check / Compose config | PASS |

全量非外部回归命令：

```bash
cd /home/hp/workspace/HpAgent_web
TMPDIR=/tmp ./.venv/bin/python -m pytest -q -m 'not external and not postgres'
```

PostgreSQL persistence 测试与开发 Worker 共用数据库时可能争抢 Outbox。验收时曾暂时停止应用
Worker，测试后已恢复并确认 `hpagent`、`hpagent-api`、`hpagent-document-worker`、`web-dev`、
`hindsight` 正常运行。

## 5. 当前未提交改动

以 `git status --short` 为准。当前包括：

```text
.env.example
docker-compose.yaml
docs/operations/file-assistant-f0-f4-implementation-map.md
docs/operations/file-assistant-f0-f3-runtime-closure-handoff.md
src/file_runtime/__init__.py
src/file_runtime/output.py
src/file_runtime/routing.py
src/orchestration/worker.py
src/persistence/repositories.py
src/sandbox/sandbox_manager.py
src/sandbox/tools/local/file_read.py
src/sandbox/tools/local/file_write.py
src/storage/tenant_file_store.py
src/web_domain/file_services.py
src/workspace/file_scope.py
test/test_document_temporal_integration.py
test/test_durable_agent_hardening.py
test/test_file_output.py
test/test_file_read_tools.py
test/test_file_version_tools.py
test/test_run_file_workspace.py
test/web_api/conftest.py
test/web_api/test_binary_file_upload.py
test/web_persistence/test_file_lineage.py
test/web_persistence/test_file_output_persistence.py
```

## 6. 新窗口接手步骤

先确认工作树：

```bash
cd /home/hp/workspace/HpAgent_web
git branch --show-current
git status --short
git diff --check
git diff --stat
```

预期分支为 `feat/hpagent-web`。不要运行 `git reset --hard`、`git checkout -- .` 或清理
untracked files。随后阅读：

```bash
sed -n '1,320p' docs/operations/file-assistant-f0-f4-implementation-map.md
sed -n '1,320p' docs/operations/file-assistant-f0-f3-runtime-closure-handoff.md
git diff
```

提交前最小复验：

```bash
./.venv/bin/python -m compileall -q src
docker compose config --quiet
git diff --check
```

如代码未变化，无需机械重复所有昂贵的 Runtime 测试；若有实质修改，运行对应 scoped tests，
必要时补跑全量回归。用户确认后再提交，建议提交信息：

```bash
git add .env.example docker-compose.yaml docs/operations/file-assistant-f0-f4-implementation-map.md \
  docs/operations/file-assistant-f0-f3-runtime-closure-handoff.md src test
git diff --cached --stat
git diff --cached --check
git commit -m "fix(file): close F0-F3 runtime gaps"
```

## 7. 后续待完成

若下一轮明确进入 F4，应复用现有 Approval Foundation：

1. 明确首个真实高风险外部动作及可查询、可 reconciliation 的目标 Adapter。
2. 增加 approval request Outbox 与 Temporal Signal/Update 契约。
3. 实现 Workflow wait 和 approve/reject/timeout/cancel 的确定性恢复。
4. 在外部 Side Effect Intent 前原子 consume 精确 grant，并继续使用 fencing/idempotency。
5. 覆盖批准前退出、consume 后/intention 前退出、外部成功但 ACK 丢失等故障窗口。
6. 最后接 Web approval inbox；不要把 UI 或数据表存在误判为 Runtime Complete。

进入 F4 前仍须遵守：不得修改已应用 migration checksum；schema 变化新增 migration 030+。
