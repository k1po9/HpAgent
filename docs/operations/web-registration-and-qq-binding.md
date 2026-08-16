# Web 注册与 QQ 自助绑定

数据库迁移 `012_self_service_identity.sql` 新增 `web_credentials` 和一次性 QQ
绑定 challenge。运行 migration 后，Web API 默认从 PostgreSQL 验证密码；注册会
原子创建 Account、Web Identity、Argon2 credential，并直接建立 HttpOnly
Session。

## 配置

API 与 QQ Worker 必须配置相同的 `QQ_BINDING_CODE_PEPPER`。生产值至少 32
字节并通过 secret manager 注入。`QQ_BINDING_CHALLENGE_SECONDS` 默认为 300。

`WEB_CREDENTIALS_JSON` 仅保留为过渡期 DB-first fallback。已有环境应先执行：

```bash
MIGRATION_DATABASE_URL=postgresql://... \
WEB_CREDENTIALS_JSON='{"alice":"$argon2id$..."}' \
python scripts/migrate_web_credentials.py
```

脚本只导入已有 active Web Identity，并且不会覆盖已存在的数据库 credential。
核实所有用户均已导入后，可移除 `WEB_CREDENTIALS_JSON`。

## 用户流程

用户可在登录页切换到注册。登录后侧栏显示 QQ 状态；点击“绑定 QQ”会生成
`HP-xxxxxx`，用户必须用待绑定 QQ 的真实账号向 HpAgent 发送：

```text
绑定 HP-xxxxxx
```

命令在 Conversation/Temporal/Agent 前被拦截。若 QQ 已属于一个历史 Account，
系统只会在新 Web Account 尚无 conversation、run、artifact 或其他业务数据时，
把 Web Identity 和 Web Session 原子迁入该历史 Account；Hindsight bank 因而继续
使用原来的 `hpagent-u-{account_id}`。一旦 Web Account 已有业务数据，自动合并会
被拒绝并保留两个 Account。
