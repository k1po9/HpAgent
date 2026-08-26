# File Workspace Agent architecture drift

基线：`hpagent-file-workspace-agent-p0-p1-guide.md` v0.1（2026-08-25）。

## 2026-08-26 实施记录

已关闭的 drift：

- FILE-P0-01：新增 `stored_files`、`message_files`、`run_files`、
  `run_budgets`、`run_usage_ledger`，包含租户/会话组合外键、状态形状、
  Message 角色约束和幂等用量主键。
- FILE-P0-02（基础部分）：新增独立 `TenantFileStore`，使用服务端生成 key、
  `.part`、流式大小/哈希/UTF-8/NUL 校验、`fsync`、原子发布与 0600/0700
  权限。尚未接入 HTTP 生命周期和 orphan 清理任务。
- FILE-P0-04（路径基础部分）：路径校验不再使用字符串前缀；绝对路径、
  `..`、同前缀目录和符号链接失败关闭。宿主机 Bash 改为独立且默认关闭
  的能力门禁。input/scratch/output 三类 capability 尚未接入 Worker。
- FILE-P0-05（`fs_read` 部分）：改为逐行读取，并在生成返回值时限制行数和
  UTF-8 字节数；其他旧文件工具仍需按相同协议改造。
- FILE-P1-01（事务基础部分）：`SendMessageRequest` 接受 `file_ids`；发送事务
  校验 ready/租户/会话/重复绑定，冻结 `message_files`、`run_files` 和预算快照；
  Retry 复制原 Run 输入。上传 API 尚未完成，因此 Feature Flag 保持默认关闭。

仍为上线阻断项：

- 上传/查询/删除/下载 HTTP API 与协议中间件窄例外。
- Worker Run 文件执行视图和 input/scratch/output 独立 capability。
- `inspect_file`、`search_file`、`count_matches`、`text_stats`、
  `transform_file`、`publish_output`。
- RunBudget reserve/settle enforcement、canonical model usage 和最终回答预留执行。
- 文件/预算 Trace allowlist、指标、清理任务、部署独立挂载和启动断言。
- 前端上传状态机、Composer、SSE phase、output 卡片和下载闭环。
- 真实 PostgreSQL migration/权限/并发测试、资源基准、故障注入和完整 E2E。

在这些阻断项关闭前，不得打开 `WEB_FILE_UPLOAD_ENABLED` 或
`WEB_FILE_TRANSFORM_ENABLED`，也不得把当前基础实现描述为 P0/P1 已完成。
