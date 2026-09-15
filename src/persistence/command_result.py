"""Persisted command envelope shared by product domains."""
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommandResult(Mapping[str, Any]):
    """Committed command body and persisted outcome code.

    response_status retains the existing PG command-envelope encoding; HTTP uses
    it directly, while other adapters interpret the outcome and resource IDs.
    """

    response_status: int
    body: dict[str, Any]
    replayed: bool = field(default=False, compare=False)
    resource_reused: bool = field(default=False, compare=False)

    def __getitem__(self, key: str) -> Any:
        return self.body[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.body)

    def __len__(self) -> int:
        return len(self.body)
