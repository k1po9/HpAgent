# Account 与模型治理运维

## 注册与 Entitlement

`POST /auth/register` 支持不填写 `invite_code` 的自助注册。`src/account/registration_service.py` 当前默认写入 `model_access_tier=standard`、`daily_token_limit=50000`、`prompt_visibility=none`，且不设置 entitlement 到期时间。填写邀请码时，注册服务验证它未撤销、未过期且尚有兑换次数，并把其中的 `entitlement_profile` 复制到新账号的 `account_entitlements`；账号、身份、凭据、entitlement 和兑换计数在同一事务中写入。无效邀请码不会回退到默认权限。

邀请码是管理员预先创建、带特定 entitlement profile 的注册凭证。`registration_invites.entitlement_profile.daily_token_limit` 只决定后续使用该凭证注册的账号初始值；已注册账号的额度存在 `account_entitlements.daily_token_limit`，后续读取与治理都以账号记录为准。修改现有邀请 profile 不会自动更新已注册账号。

## 创建邀请码

完成 Migration 后，在具备数据库访问与项目 Python 依赖的环境运行 `scripts/operations/create-registration-invite.py`。设置 `MIGRATION_DATABASE_URL`，或传入 `--database-url` 覆盖它。`--profile` 必填，表示模型访问 tier；当前普通账号的 tier 为 `standard`，`owner` 用于 owner 权限。`--daily-token-limit` 接受正整数；省略时为 `NULL`，表示不设置 Account 每日上限。`--prompt-visibility` 可取 `none`、`summary`、`full_safe`，脚本默认 `summary`。

`--entitlement-expires-at` 指复制到账号的 entitlement 到期时间；`--invite-expires-at` 指邀请码自身不能再兑换的时间，两者都要求带时区的 ISO-8601 时间。`--max-redemptions` 为可兑换次数，默认 1，必须为正整数。

```bash
export MIGRATION_DATABASE_URL='postgresql://...'
python scripts/operations/create-registration-invite.py --profile standard --daily-token-limit 50000 --prompt-visibility none
python scripts/operations/create-registration-invite.py --profile standard --daily-token-limit 200000 --max-redemptions 5
python scripts/operations/create-registration-invite.py --profile standard --invite-expires-at 2026-12-31T23:59:59Z --entitlement-expires-at 2027-01-31T23:59:59Z
```

第三个示例未传 `--daily-token-limit`，因此没有 Account 每日上限；第一个示例单次兑换，第二个可兑换五次。脚本输出 `invite_id` 和 `invite_code`。数据库只保存邀请码的 SHA-256 摘要，明文 `invite_code` 仅在创建时输出，须当场妥善保存并安全传递给注册用户。

## 两层 Token Budget

`account_entitlements.daily_token_limit` 是账号级、按 UTC calendar day 计算的模型 token 配额。正整数表示每日额度，`NULL` 表示不设置 Account Daily Token Limit。它不是单轮对话限制，也不是单个 Run 的 token budget。模型调用同时经过 Account/day budget 和 Run budget：预留额度后，按实际用量结算，未使用的预留可释放。排查额度耗尽时，先确认账号 entitlement 的状态与 `daily_token_limit`，再看 `account_daily_model_budgets`、`account_model_usage_ledger` 的 UTC `quota_date` 和对应 Run 的 `run_budgets`；不要把两层额度混为一谈。

## Prompt / Model Input 可见性

账号 `prompt_visibility` 控制模型输入查询：`none` 的 Run 列表只给最小元数据，单个 Snapshot 查询返回 403；`summary` 返回模型、端点、时间、消息和工具数量等摘要；`full_safe` 还返回已保存的 Provider 请求体。查询只限当前认证账号自己的 Run/Snapshot。`full_safe` 是请求体投影，并非额外的 Secret 脱敏步骤；不要把 API Key、认证头或其他 Secret 写入 Prompt 或模型输入。端点见 [HTTP API 参考](../reference/api.md)。

## 当前管理边界

仓库已有正式的邀请码创建脚本和底层 Account Entitlement、Daily Budget 模型。当前没有正式的 Account admin API、已有账号 entitlement 的 list/update CLI、邀请码 update/revoke CLI 或 quota management UI。排查可使用只读数据库查询、Run/Model Input API 和日志；对已有账号权限的变更尚未形成完整 Admin API / CLI，不能将创建新邀请码当成修改既有账号权限的方式。
