import uuid
from typing import Dict, Optional

from account.models import Account


class AccountService:
    """渠道 ID → 统一账号 ID 的解析服务。"""

    def __init__(self):
        self._accounts: Dict[str, Account] = {}                 # account_id → Account
        self._bindings_index: Dict[str, Dict[str, str]] = {}    # channel_type → channel_user_id → account_id

    async def resolve(self, channel_type: str, channel_user_id: str) -> str:
        """根据渠道类型和渠道用户ID查找或创建统一账号，返回 account_id。"""
        binding_key = f"{channel_type}:{channel_user_id}"
        if channel_type in self._bindings_index:
            existing = self._bindings_index[channel_type].get(channel_user_id)
            if existing:
                return existing

        account_id = str(uuid.uuid4())
        account = Account(
            account_id=account_id,
            bindings={channel_type: channel_user_id},
        )
        self._accounts[account_id] = account
        if channel_type not in self._bindings_index:
            self._bindings_index[channel_type] = {}
        self._bindings_index[channel_type][channel_user_id] = account_id

        return account_id

    async def bind_channel(
        self, account_id: str, channel_type: str, channel_user_id: str
    ) -> None:
        """为已有账号绑定新渠道。"""
        account = self._accounts.get(account_id)
        if not account:
            raise ValueError(f"Account not found: {account_id}")
        account.bindings[channel_type] = channel_user_id
        account.updated_at = __import__("time").time()
        if channel_type not in self._bindings_index:
            self._bindings_index[channel_type] = {}
        self._bindings_index[channel_type][channel_user_id] = account_id

    def get_account(self, account_id: str) -> Optional[Account]:
        return self._accounts.get(account_id)

    def find_by_binding(
        self, channel_type: str, channel_user_id: str
    ) -> Optional[Account]:
        idx = self._bindings_index.get(channel_type, {})
        account_id = idx.get(channel_user_id)
        if account_id:
            return self._accounts.get(account_id)
        return None
