"""Deployment-only values, read at existing consumer boundaries; no I/O on import."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from typing import Mapping

from common.config_validation import integer, number, typed_fields

DEFAULT_QQ_BINDING_CHALLENGE_SECONDS = 300
DEVELOPMENT_QQ_BINDING_CODE_PEPPER = b"development-qq-binding-code-pepper"


def _values(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def _integer(environment: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = environment.get(name, str(default))
    if not isinstance(raw, str):
        raise ValueError(f"{name}: expected an integer environment value")
    value = integer(raw, name)
    if value < minimum:
        raise ValueError(f"{name}: must be at least {minimum}")
    return value


@dataclass(frozen=True)
class CapacityConfig:
    work_account_accept_limit: int = 100
    work_account_coordinators: int = 4
    work_global_coordinators: int = 32
    work_account_subagents: int = 6
    work_global_subagents: int = 48
    capability_account_executions: int = 4
    capability_global_executions: int = 32
    delivery_account_capacity: int = 4
    delivery_global_capacity: int = 32
    capacity_model_global: int = 12
    capacity_model_account: int = 3
    capacity_model_interactive_reserved: int = 4
    capacity_tool_global: int = 12
    capacity_tool_account: int = 3
    capacity_tool_interactive_reserved: int = 4
    capacity_fetch_global: int = 12
    capacity_fetch_account: int = 3
    capacity_fetch_interactive_reserved: int = 4

    def __post_init__(self):
        typed_fields(type(self), asdict(self))
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"{name.upper()}: must be nonnegative")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> CapacityConfig:
        values = _values(environment)
        return cls(**{field.name: _integer(values, field.name.upper(), getattr(cls, field.name))
                      for field in fields(cls)})

    def resource_limits(self, resource: str) -> tuple[int, int, int]:
        if resource not in {"model", "tool", "fetch"}:
            raise ValueError("capacity.resource: unsupported resource")
        return (
            getattr(self, f"capacity_{resource}_global"),
            getattr(self, f"capacity_{resource}_interactive_reserved"),
            getattr(self, f"capacity_{resource}_account"),
        )


@dataclass(frozen=True)
class FileRuntimeConfig:
    file_max_bytes: int = 128 * 1024 * 1024
    file_max_count_per_message: int = 10
    file_direct_read_max_bytes: int = 1024 * 1024
    file_cleanup_interval_seconds: int = 300

    def __post_init__(self):
        typed_fields(type(self), asdict(self))
        for name, value in asdict(self).items():
            if value < 1:
                raise ValueError(f"{name.upper()}: must be positive")
        if self.file_max_bytes > 1024 * 1024 * 1024:
            raise ValueError("FILE_MAX_BYTES: must not exceed 1 GiB")
        if self.file_max_count_per_message > 20:
            raise ValueError("FILE_MAX_COUNT_PER_MESSAGE: must not exceed 20")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> FileRuntimeConfig:
        values = _values(environment)
        return cls(**{field.name: _integer(values, field.name.upper(), getattr(cls, field.name), minimum=1)
                      for field in fields(cls)})


@dataclass(frozen=True)
class ArtifactRuntimeConfig:
    artifact_html_max_bytes: int = 1024 * 1024
    artifact_model_read_timeout_seconds: float = 90.0

    def __post_init__(self):
        typed_fields(type(self), asdict(self))
        if self.artifact_html_max_bytes < 1:
            raise ValueError("ARTIFACT_HTML_MAX_BYTES: must be positive")
        if not 5 <= self.artifact_model_read_timeout_seconds <= 300:
            raise ValueError("ARTIFACT_MODEL_READ_TIMEOUT_SECONDS: must be between 5 and 300")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> ArtifactRuntimeConfig:
        values = _values(environment)
        return cls(
            _integer(values, "ARTIFACT_HTML_MAX_BYTES", cls.artifact_html_max_bytes, minimum=1),
            number(values.get("ARTIFACT_MODEL_READ_TIMEOUT_SECONDS",
                              str(cls.artifact_model_read_timeout_seconds)),
                   "ARTIFACT_MODEL_READ_TIMEOUT_SECONDS"),
        )


def run_budget_mode(environment: Mapping[str, str] | None = None, *, default: str) -> str:
    value = _values(environment).get("RUN_BUDGET_MODE", default)
    if not isinstance(value, str) or value not in {"off", "observe", "enforce"}:
        raise ValueError("RUN_BUDGET_MODE: expected off, observe, or enforce")
    return value


def binding_challenge_seconds(
    environment: Mapping[str, str] | None = None, *,
    default: int = DEFAULT_QQ_BINDING_CHALLENGE_SECONDS,
) -> int:
    return _integer(_values(environment), "QQ_BINDING_CHALLENGE_SECONDS", default, minimum=1)
