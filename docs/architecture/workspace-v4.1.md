# Workspace v4.1：长期文件与 Run 资源

Workspace 是 Account 级长期虚拟文件系统。它管理稳定目录和文件入口，不保存第三份正文。PostgreSQL 保存所有权、来源、目录、授权、候选快照、修订、操作和保留状态；TenantFileStore 保存不可变字节；RunFileWorkspace 在执行期间按需物化 inputs/scratch/outputs。普通文件流程不依赖 Git 工作区。

## 身份与来源

- `account_id` 决定所有权；来源 Conversation 可为空。生成文件关联真实 source Run，Task 可由 Run 追溯。
- `workspace_nodes.node_id` 是目录/入口身份，名称和路径只是展示。不可变 entry 指向 `file_id`；可更新 entry 指向 destination，其 revision 记录分别指向不可变对象。
- 多个 entry 可以引用同一 `file_id` 而不复制字节。保存和移动不改变文件来源、hash 或已有修订。`stored_files.purpose` 说明上传/生成来源，`run_files.direction` 说明本次 input/output 用途；已发布输出可作为后续输入。

## 授权与执行

所有者能浏览自己的 Workspace。Conversation 与 Task 用 `resource_grants` 对 entry 或递归目录授权 `list_metadata`、`read_content`、`create_child`、`update_content`、`delete_entry`。新 Conversation 没有默认长期文件 Agent 权限。摘要、claims、daily diff 和正文均须 `read_content`。

Run 创建时冻结有界候选 ID 与策略版本；超过 500 项或 512 KiB 元数据时明确拒绝。Run 搜索只过滤固定候选，不能把新移入或新授权文件加入当前 Run。首次选择原子固定对象与 revision；后续读取沿用该版本，并重新检查当前权限。候选、固定、物化、读取、发布和长期保存分别记录。撤销先阻断新访问和提交，再请求受影响 Run 停止；`stopping` 不等于已确认停止。

## 输出、版本与保留

发布是 Run 输出的持久事实，保存是随后创建 Workspace entry 或向 destination 提交新 revision。两者使用独立 operation 身份。版本更新检查预期 revision/hash 与当前 `update_content`；CAS 冲突保留已发布输出供另存，不覆盖当前版本。Task 在创建 Run 时冻结保存目标和 operation ID；required 保存成功后才能完成 Run。

移除 entry 仅移除长期入口，不能立即释放物理空间。消息、Run 绑定、历史修订、活动固定、当前 destination 与 pending 发布等仍可能保留文件。统一 GC 在文件行锁下检查引用、声明删除，再幂等清理 TenantFileStore 字节。首期历史默认保守保留，不提供绕过 GC 的物理删除 API。

所有者可用 `/api/v1/workspace/search` 按名称、MIME 类型、来源、Task、Run、日期与摘要过滤；Run Agent 用 `/api/v1/runs/{run_id}/workspace/search`，还受固定候选、当前元数据权限与摘要内容权限约束。`/api/v1/workspace/space` 按唯一 file_id 计物理字节；单文件 retention 与 entry trace 解释引用、来源、版本和操作。Research Markdown 摘要索引与 file_id/hash 绑定。

## 初始化与验收边界

首次使用幂等创建根及“资料/任务/成果”三个普通目录；被删除的默认目录不会自动重建。空库按 `persistence/migrations/` 顺序初始化。API 与 Worker 启动只读校验迁移清单及校验和；不匹配会明确失败，不自动清库或启用旧路径。

已运行的链路和剩余运行验收见 [P0～P5 总体验收](../implementation/workspace-v4.1/ACCEPTANCE.md)。
