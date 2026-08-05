# Phase B 开发说明

Phase B 提供独立的 `hpagent-api` ASGI 进程以及仅测试环境可用的确定性 Fake Run Executor。API 组合根位于 `src/web_api/`，不导入模型、Sandbox、Temporal Worker 或 ChannelRouter。

## 启动

先对空库执行 migration，并为运行角色配置密码。开发模式可运行：

```bash
export APP_DATABASE_URL=postgresql://hpagent_api:hpagent_api@localhost:5434/hpagent
export WEB_PUBLIC_ORIGIN=https://localhost
export WEB_CURSOR_SECRET='replace-with-at-least-32-bytes'
export WEB_SESSION_TOKEN_PEPPER='replace-with-at-least-32-bytes'
export WEB_CSRF_SIGNING_KEY='replace-with-at-least-32-bytes'
export WEB_CREDENTIALS_JSON='{"alice":"<argon2id-password-hash>"}'
PYTHONPATH=src python -m web_api
```

也可以使用 `docker compose --profile web up hpagent-api`。生产配置必须设置 `HPAGENT_ENV=production` 并提供至少 32 字节的三类密钥；开发默认值不能用于生产。

`WEB_CREDENTIALS_JSON` 的 key 是规范化前的 Web 登录主体，value 是 Argon2id 摘要。Account 和 active、verified 的 `provider='web'` IdentityBinding 必须由受控初始化流程预先创建；未知主体不会自动建号。

## Fake Executor

Fake Executor 默认关闭，生产环境即使设置开关也会拒绝启动。集成测试可以同时设置：

```text
HPAGENT_ENV=test
WEB_FAKE_EXECUTOR_ENABLED=true
WORKER_DATABASE_URL=postgresql://hpagent_worker:.../hpagent
WEB_FAKE_EXECUTOR_MODE=success|failure|hold
```

它只领取 `start_run`、`cancel_run` Outbox，并通过正式 `CommandService` 生命周期方法推进状态，不执行旁路 SQL，不加载模型或工具。

## 测试

```bash
make test-api
```

测试必须连接真实 PostgreSQL。`test/web_api/` 覆盖认证、CSRF/Origin、资源隔离、Idempotency-Key、cursor、ETag、消息分页、发送并发、取消、重试、刷新恢复和 Fake Executor。Controller mock 不能替代该套件。

默认开发库若存在业务表但缺少 migration 历史，不得直接补写历史；应备份后重建，或使用一次性空库测试。
