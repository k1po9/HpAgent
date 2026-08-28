# File Workspace Agent architecture drift

基线：`hpagent-file-workspace-agent-p0-p1-guide.md` v0.1（2026-08-25）。

## 2026-08-26 实施记录

已关闭的 drift：

- FILE-P0-01：新增 `stored_files`、`message_files`、`run_files`、
  `run_budgets`、`run_usage_ledger`，包含租户/会话组合外键、状态形状、
  Message 角色约束和幂等用量主键。
- FILE-P0-02（基础部分）：新增独立 `TenantFileStore`，使用服务端生成 key、
  `.part`、流式大小/哈希/UTF-8/NUL 校验、`fsync`、原子发布与 0600/0700
  权限，并已接入上传/下载生命周期。orphan 清理任务尚未实现。
- FILE-P0-03：已实现创建上传、流式 PUT、查询、逻辑删除、安全下载、窄化的
  octet-stream 协议例外、硬请求上限、CSRF、幂等创建和 opaque ownership 查询。
  真实 PostgreSQL ACL/故障注入测试仍待具备测试数据库后执行。
- FILE-P0-04（路径基础部分）：路径校验不再使用字符串前缀；绝对路径、
  `..`、同前缀目录和符号链接失败关闭。宿主机 Bash 改为独立且默认关闭
  的能力门禁。input/scratch/output 三类 capability 尚未接入 Worker。
- FILE-P0-05（`fs_read` 部分）：改为逐行读取，并在生成返回值时限制行数和
  UTF-8 字节数；其他旧文件工具仍需按相同协议改造。
- FILE-P1-01（事务基础部分）：`SendMessageRequest` 接受 `file_ids`；发送事务
  校验 ready/租户/会话/重复绑定，冻结 `message_files`、`run_files` 和预算快照；
  Retry 复制原 Run 输入。Feature Flag 在结构化工具和完整门禁完成前保持默认关闭。
- FILE-P1-02（执行范围基础）：Worker 从 authoritative `run_files` 加载 ready input，
  在独立 `FILE_RUN_ROOT/{run_id}` 下建立 0400 input、0700 scratch/output；执行范围
  在账户锁内绑定到 Run，完成、失败或取消后解绑并清理。该根目录与 file store、
  Git Workspace 任意重叠时 Worker 失败关闭。只读结构化工具已使用该 capability。
- FILE-P1-03（字面量只读工具）：已把 `inspect_file`、`search_file`、
  `count_matches`、`text_stats` 注册到 Web Run capability。扫描按固定 chunk 和
  有界单行缓冲执行，返回值同时限制行数、命中数与 UTF-8 字节；精确计数不会
  返回匹配正文。当前不开放正则；工具通过可信 registry metadata 申报扫描上界，
  Durable Activity 使用稳定 operation_id reserve，并按工具层实测扫描/返回字节
  settle。扫描中的异步取消 checkpoint 仍是启用 Feature Flag 前的阻断项。
- FILE-P0-06（工具账本与 usage 基础）：新增事务型 `RunBudgetService`，在锁定
  budget snapshot 后按 `(run_id,operation_id,dimension)` 幂等 reserve/settle/release，
  enforce 模式保留最终回答 token，observe 模式记录越界；Durable 工具路径已接入。
  模型客户端已把 Anthropic/OpenAI usage 统一为 `input_tokens`、`output_tokens`、
  `total_tokens`、`usage_source`，缺失时使用统一估算。Durable decision、规划、
  评估、HyDE 和工具摘要通过 task-local scope 接入；provider fallback 每次尝试
  独立记账，显式 `final_only` 生成可消费最终回答预留。Legacy Web 单 Activity
  路径和普通 decision 直接成为最终答复时的预留提升尚未完成。

仍为上线阻断项：

- `transform/publish` Durable 写工具；旧通用文件工具不得获得 inputs 的写能力。
- `inspect_file`、`search_file`、`count_matches`、`text_stats`、
  `transform_file`、`publish_output`。
- Legacy Web 单 Activity 的模型/工具预算，以及普通 decision 直接结束时的最终
  回答预留提升。
- 文件/预算 Trace allowlist、指标、清理任务、部署独立挂载和启动断言。
- 前端上传状态机、Composer、SSE phase、output 卡片和下载闭环。
- 真实 PostgreSQL migration/权限/并发测试、资源基准、故障注入和完整 E2E。

在这些阻断项关闭前，不得打开 `WEB_FILE_UPLOAD_ENABLED` 或
`WEB_FILE_TRANSFORM_ENABLED`，也不得把当前基础实现描述为 P0/P1 已完成。
