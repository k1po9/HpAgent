"""Channel acceptance is independent of Run completion and user acceptance."""
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ChannelSendResult:
    state: Literal['accepted', 'failed', 'uncertain']
    provider_message_id: str | None = None
    status: str | None = None
    retcode: int | None = None
    error_code: str | None = None
    receipt: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.state == 'accepted'
