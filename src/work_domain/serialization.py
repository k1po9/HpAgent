"""Pure conversion of existing Work values for JSON projections."""

from datetime import datetime
from typing import Any
from uuid import UUID


def to_json_value(value: Any) -> Any:
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    return value
