from dataclasses import dataclass, field
from typing import Dict
import time


@dataclass
class Account:
    account_id: str
    bindings: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "account_id": self.account_id,
            "bindings": self.bindings,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Account":
        return cls(
            account_id=data["account_id"],
            bindings=data.get("bindings", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
