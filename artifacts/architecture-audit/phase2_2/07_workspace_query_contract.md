# WorkspaceQueryService · 只读查询草案

> Historical architecture evidence. Not current architecture documentation.
> ACD-18 / ACD-16，TARGET DECISION / NOT IMPLEMENTED。session worktree 的物理方案是 FUTURE OPTION。

## Current / Target / Required migration/refactor

**Current：** E36 的 RunResourcePreparation 通过 PG Run → account/session/workspace_ref=account_repo 解析 repo，在账户锁内 ensure_session_workspace/recover，可能 checkout `hpagent/{session_id}`。它是执行资源准备流程，不能直接拿来实现只读 GET。E39 的 RunFileWorkspace 是另一个 inputs/scratch/outputs 范围。当前未见通用 WorkspaceQueryService 和 Conversation workspace 查询路由。

**Target：** Conversation 页面能查询“我当前会话绑定的工作空间及明确版本”，通过稳定逻辑合同访问文件树/内容/状态/diff/metadata。页面永远不接受或拼接宿主绝对路径，不直接依赖 GitRepoManager/Sandbox 实现。

**Required migration/refactor：** Workspace owner 建立逻辑 workspace binding，查询适配器在服务端解析 owner/session/ref 并做一致性检查；提供与 Run Files 独立的命名范围和有界查询。UI 与完整 worktree 隔离分别后续实现。

## 绑定与查询合同

推荐绑定对象：`workspace_id, account_id, conversation_id, session_id, logical_ref, backing_kind, binding_version`。PG 保存正式归属和逻辑映射，Git/本地适配器负责内容；不暴露内部 repo_path。可以先从现有 Session.workspace_ref 派生绑定，不要求立刻建独立 Workspace 服务进程。

Session 是执行/上下文生命周期，Conversation 可能跨 Session 轮换。首版查询返回当前 active Session 的绑定和版本，旧 Session 必须显式选择；是否轮换时继承项目 workspace 是需另评的 FUTURE OPTION，不允许前端猜映射。

| 方法 | 建议输入 | 建议输出 / 行为 |
| --- | --- | --- |
| get_metadata | account context + conversation_id + 可选 session_id | workspace_id、binding_version、Session、backing_kind、支持视图/功能、readonly、可用性；不含物理路径 |
| get_tree | workspace_id + view + revision_token + relative_path + cursor/limit | 路径/name/kind/size/可读状态、next_cursor、同一 revision_token；不无限递归目录 |
| get_file | workspace_id + view + revision_token + relative_path + offset/limit | text/metadata/binary-download reference、encoding、size、content_hash、truncated；大二进制不直接塞 JSON |
| get_status | workspace_id + view + revision_token | branch/ref 标签、dirty/changed counts、available/busy/not_active、版本；不把无可用工作树返回为空目录 |
| get_diff | workspace_id + view + revision_token + relative_path 或受限路径列表 | 已绑定 base/head 的有界 diff、truncated、binary 状态；不接受客户端任意 Git ref/path/参数 |

账户身份来自服务端认证；workspace_id 不构成授权能力。每次验证 account → Conversation → Session → binding 的归属，版本过期返回明确 `version_changed`/相应可重试结果，由调用者刷新 metadata。错误码和最终 HTTP 路由在实现设计时固定，不在本审计宣称已有接口。

## 两种视图及一致性

**Committed view：** 对确定 commit/ref 的不可变查询。get_tree/get_file/get_diff 读取同一 commit，pagination token 绑定 revision。查询某旧 Session 不需切换共享 checkout，可通过只读 Git 对象读取其 committed 内容。

**Working view：** 包括属于该 Session 的未提交更改；需要 workspace owner 提供 read token/内容一致性。当前共享账户 checkout 下，只能在短时同账户锁内核验 expected branch 与实际分支一致后取样，不能调用会 checkout 的 execution preparation。非当前 Session 的 dirty 内容返回 `not_active`/`working_view_unavailable`，而不是读取当前另一个 Session 的文件。

跨多次 API 请求不持有长锁。可用短时物化只读快照，或分支/内容 fingerprint + 每次重验的 token；外部编辑若无法保证一致视图，则明确返回变化并要求刷新，不能用一个不反映内容变化的内存计数冒充一致性。具体方案随文件规模验证后选择。Session/分支切换、同账户两个 Conversation 并发查询都要有测试。

即使 API 与 Agent 分进程，读工作树也要遵守同一资源 owner；可由受控只读适配器读取不可变 Git refs，或经 owner 取得物化快照。不能默认 API 的 Python 内存锁与 Worker 的 AccountLockRegistry 是同一个锁。未选定工作视图跨进程一致性实现前，正式支持 committed view + 明确 working unavailable 即可，不冒称实时工作区已支持。

## Persistent Workspace 与 Run Files

| 名称空间 | 对象 | 用户可见范围 |
| --- | --- | --- |
| Workspace | Git/Account/Session 的 project/source/persistent edits | 显式 committed 或有保证的 working 视图 |
| Run Files | 某 Run 的 uploaded inputs / generated outputs / scratch | 输入/输出按 Run ownership 查询；scratch 默认不暴露，调试角色按明确规则有限查询 |
| Persistent file revisions | File owner 的 logical path/revision/immutable object | 作为文件版本资产单独引用，不自动映射成 Git workspace 文件 |

前端可显示两个主要区块 Workspace / Run Files，后端仍用不同 scope kind/ID。资产导入工作树或将工作树文件发布为输出是显式命令，不由读取接口顺便完成。

## 查询范围与权限

逻辑路径拒绝绝对路径/越界段；符号链接不能逃逸授权根，.git 内部对象/凭证/执行临时目录不作为普通文件树暴露。大文件、二进制、分页和 diff 必须可截断并说明结果不完整；不是为了防御所有未来风险，而是只读文件浏览的真实输入边界。

GET 不执行 shell、checkout、reset、provision、格式转换或文件写入。下载引用也带 ownership 检验，不能泄露宿主文件路径。授权变化后不能继续用旧分页/快照凭据跨权限读文件。User/Developer 查询差异是访问投影，不改变工作空间归属。

## Session worktree 演进候选

```text
Account repository
  ├─ Session / Conversation A worktree
  ├─ Session / Conversation B worktree
  └─ Session / Conversation C worktree
```

此模型可减少分支切换与实时视图冲突，具有明确需求动机；当前未实现，也未定为唯一长期方案。须评估 Session/Conversation 绑定粒度、未提交文件归属、生命周期回收、并发锁和磁盘成本，经 G07 后才能开放配置。Query 层用 workspace_id/ref 隔开这项物理变化，不让 UI 依赖目录结构。

G13 的验收必须检查“查询没有切换/修改工作树”，并模拟两会话共享账户、非活动 Session、分支切换与内容变化；只测试 os.listdir 返回列表不能证明产品查询合同。
