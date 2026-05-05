"""
AccountService —— 渠道身份到统一账号的解析服务。

核心职责：
  1. resolve(channel_type, channel_user_id) → account_id
     查询 bindings_index：如果该渠道用户已有关联账号，直接返回；
     否则创建新 Account 并建立绑定。
  2. bind_channel(account_id, channel_type, channel_user_id)
     为已有账号追加新的渠道绑定（如手机号绑定到已有 QQ 账号）。
  3. find_by_binding(channel_type, channel_user_id) → Account | None
     纯查询，不创建。

数据结构：
  - _accounts: Dict[account_id, Account]  —— 主存储
  - _bindings_index: Dict[channel_type, Dict[channel_user_id, account_id]]  —— 反向索引，加速查找

当前实现为纯内存存储（进程重启丢失），后续接入 storage.InfraContainer
中的 KeyValueStore 或 PostgreSQL 实现持久化。
"""
import uuid
from typing import Dict, Optional

from account.models import Account


class AccountService:
    """渠道 ID → 统一账号 ID 的解析与绑定服务。

    用法示例::

        svc = AccountService()
        account_id = await svc.resolve("napcat", "123456")  # QQ 用户首次 → 新建
        account_id = await svc.resolve("web", "user_abc")    # Web 用户 → 新建

        # 将 Web 身份绑定到已有 QQ 账号，实现跨渠道关联
        await svc.bind_channel(account_id, "web", "user_abc")
    """

    def __init__(self):
        # 主存储：account_id → Account 实体
        self._accounts: Dict[str, Account] = {}
        # 反向索引：channel_type → (channel_user_id → account_id)
        # O(1) 查找，避免遍历 _accounts 的 bindings
        self._bindings_index: Dict[str, Dict[str, str]] = {}

    async def resolve(self, channel_type: str, channel_user_id: str) -> str:
        """根据渠道类型和渠道用户 ID 查找或创建统一账号。

        查找流程：
          1. 在 _bindings_index[channel_type] 中查 channel_user_id
          2. 命中 → 直接返回 account_id
          3. 未命中 → 生成新 UUID → 创建 Account → 更新两个索引 → 返回 account_id

        Args:
            channel_type: 渠道类型字符串，如 "napcat"、"web"、"console"。
            channel_user_id: 渠道侧的用户标识，如 QQ 号 "123456"。

        Returns:
            全局唯一的 account_id（新创建或已有）。
        """
        # 1. 查反向索引 —— O(1)
        if channel_type in self._bindings_index:
            existing = self._bindings_index[channel_type].get(channel_user_id)
            if existing:
                return existing

        # 2. 新建账号
        account_id = str(uuid.uuid4())
        account = Account(
            account_id=account_id,
            bindings={channel_type: channel_user_id},
        )
        # 3. 更新主存储
        self._accounts[account_id] = account
        # 4. 更新反向索引
        if channel_type not in self._bindings_index:
            self._bindings_index[channel_type] = {}
        self._bindings_index[channel_type][channel_user_id] = account_id

        return account_id

    async def bind_channel(
        self, account_id: str, channel_type: str, channel_user_id: str
    ) -> None:
        """为已有账号追加一个新渠道绑定。

        例如：用户先用 QQ 登录（已有 account_id），再通过手机号验证，
        将手机号绑定到同一 account_id，实现跨端身份统一。

        Args:
            account_id: 目标账号 ID。
            channel_type: 新渠道类型。
            channel_user_id: 新渠道的用户标识。

        Raises:
            ValueError: account_id 不存在。
        """
        account = self._accounts.get(account_id)
        if not account:
            raise ValueError(f"Account not found: {account_id}")

        # 更新 Account 实体
        account.bindings[channel_type] = channel_user_id
        account.updated_at = __import__("time").time()

        # 更新反向索引
        if channel_type not in self._bindings_index:
            self._bindings_index[channel_type] = {}
        self._bindings_index[channel_type][channel_user_id] = account_id

    def get_account(self, account_id: str) -> Optional[Account]:
        """按 account_id 查询 Account 实体。"""
        return self._accounts.get(account_id)

    def find_by_binding(
        self, channel_type: str, channel_user_id: str
    ) -> Optional[Account]:
        """按渠道身份反查 Account 实体（纯查询，不创建）。"""
        idx = self._bindings_index.get(channel_type, {})
        account_id = idx.get(channel_user_id)
        if account_id:
            return self._accounts.get(account_id)
        return None
