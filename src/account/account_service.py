"""
AccountService —— 渠道身份到统一账号的解析服务，JSON 文件持久化。

持久化文件: data/accounts.json（宿主机可见）
格式: {"<account_id>": {"bindings": {"napcat": "qq号"}, ...}, ...}

索引 _bindings_index 在加载时从 bindings 反向重建。
"""
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Dict, Optional

from account.models import Account

logger = logging.getLogger("HpAgent.Account")


class AccountService:
    """渠道 ID → 统一账号 ID 的解析与绑定服务。

    用法::

        svc = AccountService(data_dir=Path(".data/data"))
        account_id = await svc.resolve("napcat", "123456")  # QQ → UUID
        await svc.bind_channel(account_id, "web", "user_abc")
    """

    def __init__(self, data_dir: Path | str = Path(".data/data")):
        self._data_dir = Path(data_dir)
        self._file = self._data_dir / "accounts.json"
        self._accounts: Dict[str, Account] = {}
        self._bindings_index: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        """从 JSON 文件恢复账号和绑定索引。"""
        if not self._file.exists():
            logger.info("No accounts file at %s — starting fresh", self._file)
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read accounts file %s: %s", self._file, e)
            return

        for account_id, data in raw.items():
            self._accounts[account_id] = Account.from_dict(data)
            for ch_type, ch_uid in data.get("bindings", {}).items():
                self._bindings_index.setdefault(ch_type, {})[ch_uid] = account_id

        logger.info("Loaded %d accounts from %s", len(self._accounts), self._file)

    def _save(self) -> None:
        """全量写回 JSON 文件（原子写: .tmp → rename）。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        try:
            payload = {aid: acct.to_dict() for aid, acct in self._accounts.items()}
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._file)
        except OSError as e:
            logger.error("Failed to save accounts: %s", e)

    async def resolve(self, channel_type: str, channel_user_id: str) -> str:
        """查找或创建统一账号。命中返回已有 UUID，未命中新建并持久化。"""
        if channel_type in self._bindings_index:
            existing = self._bindings_index[channel_type].get(channel_user_id)
            if existing:
                return existing

        account_id = str(uuid.uuid4())
        account = Account(account_id=account_id, bindings={channel_type: channel_user_id})
        self._accounts[account_id] = account
        self._bindings_index.setdefault(channel_type, {})[channel_user_id] = account_id

        logger.info("New account %s bound to %s:%s", account_id, channel_type, channel_user_id)
        self._save()
        return account_id

    async def bind_channel(
        self, account_id: str, channel_type: str, channel_user_id: str
    ) -> None:
        """为已有账号追加新渠道绑定。"""
        account = self._accounts.get(account_id)
        if not account:
            raise ValueError(f"Account not found: {account_id}")

        account.bindings[channel_type] = channel_user_id
        self._bindings_index.setdefault(channel_type, {})[channel_user_id] = account_id

        logger.info("Account %s added binding %s:%s", account_id, channel_type, channel_user_id)
        self._save()

    def get_account(self, account_id: str) -> Optional[Account]:
        return self._accounts.get(account_id)

    def list_all_ids(self) -> list[str]:
        """返回所有已注册账号的 ID 列表。"""
        return list(self._accounts.keys())

    def find_by_binding(
        self, channel_type: str, channel_user_id: str
    ) -> Optional[Account]:
        idx = self._bindings_index.get(channel_type, {})
        account_id = idx.get(channel_user_id)
        if account_id:
            return self._accounts.get(account_id)
        return None
