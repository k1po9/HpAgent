"""
Account 数据模型 —— 统一账号实体。

设计要点：
  - account_id 为 UUID，由 AccountService 在首次解析时生成
  - bindings 为 Dict[str, str]，键是渠道类型（如 "napcat"、"web"），值是渠道用户 ID（如 QQ 号）
  - 同一 account_id 可绑定多个渠道，实现跨平台用户身份统一

与旧实现的区别：
  - 旧实现中 user_id 直接使用渠道 sender_id（如 QQ 号），Web 和 QQ 无法关联
  - 新实现中 account_id 是独立 UUID，通过 bindings 映射多渠道身份
"""
from dataclasses import dataclass, field
from typing import Dict
import time


@dataclass
class Account:
    """统一用户账号实体。

    一个 Account 可以绑定多个渠道身份（QQ、手机、Web 等），
    所有渠道路由到同一个 Temporal Workflow (workflow_id = f"agent-{account_id}")。

    Attributes:
        account_id: 全局唯一的账号 UUID，由 AccountService.resolve() 首次生成。
        bindings: 渠道类型 → 渠道用户 ID 的映射，如 {"napcat": "123456", "web": "user_abc"}。
        created_at: 账号首次创建时间戳（Unix epoch float）。
        updated_at: 账号最后更新时间戳（绑定新渠道时刷新）。
    """
    account_id: str
    bindings: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        """序列化为字典，用于 JSON 持久化。"""
        return {
            "account_id": self.account_id,
            "bindings": self.bindings,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Account":
        """从字典反序列化，用于从文件/数据库加载。"""
        return cls(
            account_id=data["account_id"],
            bindings=data.get("bindings", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
