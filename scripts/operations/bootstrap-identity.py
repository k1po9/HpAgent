#!/usr/bin/env python3
"""
管理员预置或恢复 Web/QQ 与同一 PostgreSQL Account 的身份绑定。

绑定建立后，Web 与 QQ 解析到同一个 ``accounts.account_id``，
共享同一个 Hindsight bank（``hpagent-u-{account_id}``）。

用法:
    MIGRATION_DATABASE_URL=postgresql://hpagent_migrate:...@localhost:5434/hpagent \
    python scripts/operations/bootstrap-identity.py \
      --web-subject huangpei \
      --qq-channel napcat \
      --qq-subject 123456789

正常用户绑定使用 API 的 QQ ownership challenge；此工具凭管理员数据库权限直接
写入已核实的身份，不能替代 QQ 所有权验证。两端都不存在时会创建 owner
entitlement，但不会创建 Web 登录密码。重复执行幂等；两端属于不同 Account
时退出非零码，不执行 Account 合并。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from account.bootstrap import bootstrap_identity  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Admin bootstrap/recovery of verified Web and QQ bindings on one Account"
    )
    parser.add_argument("--web-subject", required=True, help="Web username (e.g. huangpei)")
    parser.add_argument(
        "--qq-channel", required=True, choices=("napcat", "official_qq"),
        help="QQ channel type",
    )
    parser.add_argument("--qq-subject", required=True, help="QQ number or OpenID")
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_DATABASE_URL"),
        help="Admin/migration PostgreSQL DSN (default MIGRATION_DATABASE_URL)",
    )
    args = parser.parse_args()

    if not args.database_url:
        parser.error(
            "database-url is required; set MIGRATION_DATABASE_URL or pass --database-url"
        )

    result = bootstrap_identity(
        args.database_url,
        web_subject=args.web_subject,
        qq_channel=args.qq_channel,
        qq_subject=args.qq_subject,
    )
    print(f"bootstrap_identity: status={result.status} account_id={result.account_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        print(f"bootstrap_identity: FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
