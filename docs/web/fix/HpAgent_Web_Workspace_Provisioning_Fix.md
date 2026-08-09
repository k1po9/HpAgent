# HpAgent Web Workspace Provisioning 修复方案

## 1. 目标

修复 Web 新建会话并发送消息后，Run 在 `execute_agent_activity` 阶段失败：

```text
workspace_recovery_required
workspace is not a valid Git worktree
```

目标不是绕过 `WorkspaceRecoveryGuard`，而是补齐 **Web Session 对本地 workspace / Git branch 的安全 provisioning 生命周期**。

---

## 2. 已确认根因

真实链路已经到达：

```text
Browser
-> web-gateway
-> hpagent-api
-> PostgreSQL Run
-> start_run Outbox
-> Temporal WebRunWorkflow
-> execute_agent_activity
-> WebExecutionHost
-> SessionResourceRecoveryService.lease_for_run()
-> WorkspaceRecoveryGuard.recover()
-> FAILED
```

当前 Web `send_message()` 只创建 PostgreSQL canonical state：

```text
Conversation -> Session -> Message -> Run -> start_run Outbox
```

但没有创建本地：

```text
/app/.data/workspace/{account_id}/repo/.git
hpagent/{session_id}
```

执行阶段却直接假设这两个资源已经存在：

```python
repo_path = workspace_root / account_id / "repo"
expected_branch = f"hpagent/{session_id}"
await WorkspaceRecoveryGuard(repo_path).recover(expected_branch)
```

因此 fresh Account / fresh Web Session 会失败。

QQ 路径没有这个问题，因为 `ConversationService._prepare_session_resources()` 会调用：

```python
await git_repo_manager.ensure_repo(account_id)
await git_repo_manager.start_session(account_id, session_id)
```

所以这是 **Web Session provisioning 缺失**，不是模型、Temporal、Redis、Hindsight、SSE 或 IdentityBinding 问题。

---

## 3. 必须保持的架构约束

### 3.1 Worker owns workspace

`hpagent-api` 不允许直接：

- `git init`
- checkout/create branch
- 创建 Sandbox
- 操作 `/app/.data/workspace`

Web API 继续只负责 HTTP/Auth、PostgreSQL canonical state 和 Outbox。

### 3.2 保持单 Worker + AccountLockRegistry

当前：

```text
WORKSPACE_ISOLATION_MODE=single_process_account_lock
```

QQ 与 Web Agent Host 必须继续共享同一 `AccountLockRegistry`。

禁止为了修复：
- 新增第二个 Agent Worker；
- Web 单独创建 workspace worker；
- 多进程并发操作同一个 account repo。

### 3.3 PostgreSQL 仍是 Session / Run authoritative truth

Worker 必须通过：

```text
run_id -> account_id -> session_id -> workspace_ref
```

确定本次执行资源。

禁止根据当前 Git branch、Redis active session、QQ Session 等反推 Web Session。

### 3.4 Recovery 继续 fail closed

保留现有原则：
- 不 `git reset --hard`
- 不 `git clean`
- 不删除用户文件
- dirty wrong-branch 不强切
- unfinished merge/rebase/cherry-pick 不自动吞
- 未知 `index.lock` 所有权时继续失败

修复必须是：

> 缺失资源时安全 provision；状态异常时继续 fail closed。

---

## 4. 推荐设计

Worker 侧 Web 资源准备流程改成：

```text
SessionResourceRecoveryService.lease_for_run()

1. 从 PostgreSQL 读取 authoritative context
2. 获取 account lock
3. 安全 provision 缺失 repo / session branch
4. WorkspaceRecoveryGuard.recover()
5. 创建 Session Sandbox
6. yield，并在整个 Agent execution 期间继续持锁
```

职责建议：

```text
GitRepoManager / WorkspaceProvisioner
    -> 仅负责“可证明安全的缺失资源创建”

WorkspaceRecoveryGuard
    -> 负责“已有资源的保守恢复与验证”
```

不要让 `WorkspaceRecoveryGuard` 自己遇到无 `.git` 就直接 `git init`。

---

## 5. 建议修改文件

优先最小范围：

```text
src/workspace/isolation.py
src/sandbox/git_repo.py
src/orchestration/worker.py   # 仅依赖注入需要时
tests/...workspace...
tests/...web...
```

除非依赖注入实际要求，否则不要改：
- DB schema
- Web API contract
- Temporal Workflow contract
- Outbox contract
- Identity model
- Hindsight
- SSE
- 前端

---

## 6. 具体实现要求

### A. 增加安全 provisioning API

不要在 Web recovery 中简单无条件调用现有：

```python
ensure_repo()
start_session()
```

因为 `start_session()` 会主动 checkout；如果未来 repo 处于其他 Session 的 dirty 状态，可能破坏 fail-closed 语义。

推荐增加明确 API，例如：

```python
async def ensure_session_workspace(
    self,
    account_id: str,
    session_id: str,
) -> None:
    ...
```

或拆成：

```python
provision_repo_if_missing(...)
provision_session_branch_if_missing(...)
```

### B. repo provisioning 规则

#### Case 1：repo 路径完全不存在

允许：
```text
mkdir
git init
git config user.name/user.email
empty root commit
创建 hpagent/{session_id}
```

#### Case 2：repo 已是有效 Git repo

继续 branch 检查。

#### Case 3：repo 目录存在但不是 Git repo

如果目录非空且内容来源不可证明安全：

```python
raise WorkspaceRecoveryRequired(
    "workspace directory exists but is not a valid Git worktree"
)
```

禁止直接 `git init` 把未知目录“收编”。

### C. branch provisioning 规则

目标 branch：

```text
hpagent/{session_id}
```

若已存在：不重复创建，交给 RecoveryGuard。

若不存在，仅在以下状态下创建：
- 无 unfinished Git operation
- 无未知 `index.lock`
- working tree clean

如果当前是其他 Session branch 且 clean：
- 可以安全切到 base/default branch；
- 创建 `hpagent/{session_id}`。

如果 dirty：
```text
workspace_recovery_required
```

禁止：
- stash
- reset
- clean
- force checkout

### D. 修改 SessionResourceRecoveryService

当前：

```python
async with self._account_locks.hold(account_text, control):
    await WorkspaceRecoveryGuard(repo_path).recover(expected_branch)
    self._sandbox_manager.create_session_sandbox(...)
    yield session_id
```

建议改成类似：

```python
async with self._account_locks.hold(account_text, control):
    await self._workspace_provisioner.ensure_for_session(
        account_id=account_text,
        session_id=str(session_id),
    )

    await WorkspaceRecoveryGuard(repo_path).recover(expected_branch)

    self._sandbox_manager.create_session_sandbox(
        session_id=str(session_id),
        workspace_path=str(repo_path),
        user_uuid=account_text,
        session_context={
            "account_id": account_text,
            "channel_type": "web",
            "metadata": {},
        },
    )

    yield session_id
```

**Provisioning 必须发生在 account lock 内。**

否则 QQ 与 Web 同时操作同一 account repo 时仍可能发生 `git checkout/git branch` 竞争。

### E. 依赖注入

`SessionResourceRecoveryService` 建议增加：

```python
git_repo_manager: GitRepoManager
```

或：

```python
workspace_provisioner: WorkspaceProvisioner
```

优先复用现有 `GitRepoManager`，不要复制第二套 subprocess Git 实现。

---

## 7. 禁止的修法

禁止：

```python
try:
    recover()
except WorkspaceRecoveryRequired:
    git reset --hard
```

禁止：

```bash
rm -rf repo
git init
```

禁止：
- 在 `hpagent-api` 初始化 workspace；
- 把 Web `session_id` 替换成 QQ active session；
- 关闭 `WorkspaceRecoveryGuard`；
- 关闭 `AccountLockRegistry`；
- 关闭 workspace isolation；
- branch 不存在时直接用当前 branch 执行。

Web Run 必须执行在数据库绑定 Session 对应的：

```text
hpagent/{session_id}
```

---

## 8. 必须补的回归测试

### Test 1：fresh Account + 第一次 Web Conversation

前置：
```text
PostgreSQL Account/Web identity 存在
workspace/{account_id}/repo 不存在
```

期望：
```text
repo 自动创建
.git 存在
root commit 存在
hpagent/{session_id} 自动创建
Run 可继续执行
不再 workspace_recovery_required
```

### Test 2：同 Conversation 第二条消息

期望：
```text
不重复初始化 repo
不创建新 branch
复用同一 Web Session
正常执行
```

### Test 3：新 Conversation

同 Account，新 DB Session：

```text
session_A
session_B
```

期望：
```text
复用 repo
自动创建 hpagent/{session_B}
正常执行
```

### Test 4：expected branch 已存在

期望：
```text
直接 recover
不重复创建
```

### Test 5：错误 branch + clean

```text
current = hpagent/session_A
expected = hpagent/session_B
working tree clean
```

期望：
- B 已存在 -> 安全 checkout B
- B 不存在 -> 安全创建 B

### Test 6：错误 branch + dirty

必须：
```text
workspace_recovery_required
```

并验证：
```text
文件未删除
未 reset
branch 未被强制切换
```

### Test 7：unfinished Git operation

存在任一：
```text
MERGE_HEAD
REBASE_HEAD
CHERRY_PICK_HEAD
```

必须 fail closed。

### Test 8：未知 index.lock

保持现有：
```text
workspace_recovery_required
```

### Test 9：非 Git 的非空 repo 目录

例如：
```text
repo/foo.txt
没有 .git
```

必须 fail closed，不能自动 `git init`。

### Test 10：QQ 回归

QQ 原有：
```text
ensure_repo
start_session
Agent execution
```

必须保持正常。

---

## 9. 真实环境验收

当前已绑定账号：

```text
account_id = 8caed6bc-2bc7-4abe-b4d8-72440a3fa1a7
web = huangpei
qq/napcat = 2109279314
```

建议另建测试 Account 或确认 workspace 无价值后测试 fresh case，不要为了测试误删已有用户数据。

### Web 测试

新建 Conversation，发送：

```text
你好，请只回复：Web Agent 测试成功
```

期望：
```text
Run completed
Assistant 正常回复
```

日志中不得再出现：
```text
workspace is not a valid Git worktree
workspace_recovery_required
```

随后：
1. 同 Conversation 再发一条；
2. 新建第二个 Conversation；
3. 确认两个 Web Session 都有各自 `hpagent/{session_id}` branch。

---

## 10. 完成验收标准

- [ ] fresh Web Account 首次聊天无需人工初始化 workspace
- [ ] fresh Web Conversation 自动 provision session branch
- [ ] 同 Conversation 正常复用 branch
- [ ] 新 Conversation 自动创建新 branch
- [ ] provisioning 全程位于 AccountLockRegistry 锁内
- [ ] hpagent-api 不操作本地 workspace
- [ ] PostgreSQL Session / Run 仍为 authoritative truth
- [ ] dirty wrong-branch 继续 fail closed
- [ ] unfinished Git operation 继续 fail closed
- [ ] 未知 index.lock 继续 fail closed
- [ ] 不使用 reset --hard / clean / rm -rf 恢复
- [ ] QQ workspace 生命周期回归通过
- [ ] Web Run 最终可 completed
- [ ] Temporal / Outbox / SSE / Hindsight contract 不改变

---

## 11. 给 Flash 的实施要求

先阅读当前实现：

```text
src/workspace/isolation.py
src/sandbox/git_repo.py
src/application/conversation.py
src/agent_execution/web_host.py
src/orchestration/web_activities.py
src/orchestration/worker.py
src/web_domain/services.py
src/web_domain/sessions.py
```

先用测试复现，再最小范围修复。

不要顺手：
- 大规模重构 workspace 模块
- 修改 DB schema
- 修改 Web API contract
- 修改 Temporal Workflow contract
- 修改 Hindsight
- 修改 SSE
- 修改前端
- 修改 identity model
- 引入新的 Worker 进程
- 改 workspace isolation mode

完成后报告：

```text
1. Root cause
2. Files changed
3. Exact provisioning lifecycle after fix
4. Safety invariants preserved
5. Tests added
6. Test commands + results
7. Remaining risks / debt
```

如发现当前代码与本方案假设不一致，以当前代码为准，但必须保持：

```text
Worker owns workspace
PostgreSQL owns Session truth
Provision only when absence is provably safe
Recovery never discards user state
Account lock covers provisioning + execution
```
