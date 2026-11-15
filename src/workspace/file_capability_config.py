"""Fail-closed startup contract for global file capability roots and flags."""
from __future__ import annotations

import os
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Mapping

from common.config_validation import environment_name
from common.runtime_config import (
    FileRuntimeConfig,
    deprecated_file_flag,
    file_capability_flag,
    normalize_file_flag,
    run_budget_mode,
)
from workspace.file_scope import RunFileScopeUnavailable


@dataclass(frozen=True)
class FileCapabilityConfig:
    # InitVars keep legacy positional/keyword construction without storing duplicate state.
    upload_enabled: InitVar[bool | None] = None
    transform_enabled: InitVar[bool | None] = None
    shell_enabled: InitVar[bool | None] = None
    store_root: Path | None = None
    run_root: Path | None = None
    max_bytes: int = FileRuntimeConfig.file_max_bytes
    file_capability_enabled: bool | None = field(default=None, kw_only=True)
    file_transform_enabled: bool | None = field(default=None, kw_only=True)
    file_shell_enabled: bool | None = field(default=None, kw_only=True)

    def __post_init__(self, upload_enabled, transform_enabled, shell_enabled) -> None:
        for name, alias, legacy_value in (
            ("file_capability_enabled", "upload_enabled", upload_enabled),
            ("file_transform_enabled", "transform_enabled", transform_enabled),
            ("file_shell_enabled", "shell_enabled", shell_enabled),
        ):
            object.__setattr__(self, name, normalize_file_flag(
                getattr(self, name), legacy_value, name, alias,
            ))

    def __getattribute__(self, name: str):
        aliases = {"upload_enabled": "file_capability_enabled",
                   "transform_enabled": "file_transform_enabled",
                   "shell_enabled": "file_shell_enabled"}
        if name in aliases:
            deprecated_file_flag(name, aliases[name])
            name = aliases[name]
        return object.__getattribute__(self, name)

    @classmethod
    def from_environment(
        cls,
        workspace_root: Path | str,
        worker_database_url: str | None,
        environment: Mapping[str, str] | None = None,
    ) -> "FileCapabilityConfig":
        values = environment if environment is not None else os.environ
        try:
            upload = file_capability_flag(values, "FILE_CAPABILITY_ENABLED", "WEB_FILE_UPLOAD_ENABLED")
            transform = file_capability_flag(values, "FILE_TRANSFORM_ENABLED", "WEB_FILE_TRANSFORM_ENABLED")
            shell = file_capability_flag(values, "FILE_SHELL_ENABLED", "WEB_FILE_SHELL_ENABLED")
        except ValueError as exc:
            raise RunFileScopeUnavailable(str(exc)) from None
        if transform and not upload:
            raise RunFileScopeUnavailable("file transforms require file uploads")
        if shell and not upload:
            raise RunFileScopeUnavailable("Web file shell requires file uploads")
        try:
            deployment = environment_name(values.get("HPAGENT_ENV", "development"))
        except ValueError as exc:
            raise RunFileScopeUnavailable(str(exc)) from None
        if deployment == "production" and shell:
            raise RunFileScopeUnavailable("host Bash is forbidden for production Web runs")
        if deployment == "production" and upload:
            try:
                mode = run_budget_mode(values, default="observe")
            except ValueError as exc:
                raise RunFileScopeUnavailable(str(exc)) from None
            if mode != "enforce":
                raise RunFileScopeUnavailable(
                    "production file capability requires RUN_BUDGET_MODE=enforce"
                )
        try:
            maximum = FileRuntimeConfig.from_environment(values).file_max_bytes
        except ValueError as exc:
            raise RunFileScopeUnavailable(str(exc)) from None
        if not upload:
            return cls(file_capability_enabled=False, file_transform_enabled=transform,
                       file_shell_enabled=shell, max_bytes=maximum)
        store_text = values.get("FILE_STORE_ROOT")
        run_text = values.get("FILE_RUN_ROOT")
        if not worker_database_url or not store_text or not run_text:
            raise RunFileScopeUnavailable(
                "file capability requires WORKER_DATABASE_URL, FILE_STORE_ROOT and FILE_RUN_ROOT"
            )
        if deployment == "production" and (
            not Path(store_text).is_absolute() or not Path(run_text).is_absolute()
        ):
            raise RunFileScopeUnavailable(
                "production file roots must be explicit absolute paths"
            )
        workspace = Path(workspace_root).resolve()
        store = Path(store_text).resolve()
        run = Path(run_text).resolve()
        roots = {"Git workspace": workspace, "file store": store, "Run root": run}
        items = list(roots.items())
        for index, (left_name, left) in enumerate(items):
            for right_name, right in items[index + 1:]:
                if (
                    left == right
                    or left.is_relative_to(right)
                    or right.is_relative_to(left)
                ):
                    raise RunFileScopeUnavailable(
                        f"{left_name} and {right_name} must not overlap"
                    )
        return cls(file_capability_enabled=True, file_transform_enabled=transform,
                   file_shell_enabled=shell, store_root=store, run_root=run, max_bytes=maximum)
