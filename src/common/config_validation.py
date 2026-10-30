"""Small, value-safe checks for process configuration; no runtime I/O."""
from __future__ import annotations

import math
import types
from typing import Any, Union, get_args, get_origin, get_type_hints


def environment_name(value: str) -> str:
    if not isinstance(value, str) or value.strip().casefold() not in {
        "development", "test", "production",
    }:
        raise ValueError("HPAGENT_ENV: expected development, test, or production")
    return value.strip().casefold()


def boolean(value: str, path: str) -> bool:
    if not isinstance(value, str) or value.casefold() not in {"true", "false"}:
        raise ValueError(f"{path}: expected true or false")
    return value.casefold() == "true"


def integer(value: str, path: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{path}: expected an integer") from None


def number(value: str, path: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{path}: expected a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{path}: expected a finite number")
    return result


def _matches(value: Any, annotation: Any) -> bool:
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation is Any:
        return True
    if origin in (Union, types.UnionType):
        return any(_matches(value, choice) for choice in args)
    if origin is list:
        return isinstance(value, list) and all(_matches(item, args[0]) for item in value)
    if origin is dict:
        return isinstance(value, dict) and all(
            _matches(key, args[0]) and _matches(item, args[1]) for key, item in value.items()
        )
    if annotation is float:
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    if annotation in (int, bool):
        return type(value) is annotation
    return isinstance(value, annotation)


def typed_fields(config_type: type, values: dict[str, Any], prefix: str = "") -> None:
    for name, annotation in get_type_hints(config_type).items():
        if name in values and not _matches(values[name], annotation):
            path = f"{prefix}.{name}" if prefix else name
            raise ValueError(f"{path}: invalid type or non-finite number")
