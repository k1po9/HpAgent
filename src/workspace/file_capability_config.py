"""Fail-closed startup contract for Web file capability roots and flags."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from workspace.file_scope import RunFileScopeUnavailable


def _flag(environment: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = environment.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized not in {"true", "false"}:
        raise RunFileScopeUnavailable(f"{name} must be exactly true or false")
    return normalized == "true"


@dataclass(frozen=True)
class FileCapabilityConfig:
    upload_enabled: bool
    transform_enabled: bool
    shell_enabled: bool
    store_root: Path | None
    run_root: Path | None
    max_bytes: int

    @classmethod
    def from_environment(
        cls,
        workspace_root: Path | str,
        worker_database_url: str | None,
        environment: Mapping[str, str] | None = None,
    ) -> "FileCapabilityConfig":
        values = environment if environment is not None else os.environ
        upload = _flag(values, "WEB_FILE_UPLOAD_ENABLED")
        transform = _flag(values, "WEB_FILE_TRANSFORM_ENABLED")
        shell = _flag(values, "WEB_FILE_SHELL_ENABLED")
        if transform and not upload:
            raise RunFileScopeUnavailable("file transforms require file uploads")
        if shell and not upload:
            raise RunFileScopeUnavailable("Web file shell requires file uploads")
        deployment = values.get("HPAGENT_ENV", "development").strip().casefold()
        if deployment == "production" and shell:
            raise RunFileScopeUnavailable("host Bash is forbidden for production Web runs")
        if deployment == "production" and upload:
            if values.get("RUN_BUDGET_MODE", "observe").strip().casefold() != "enforce":
                raise RunFileScopeUnavailable(
                    "production file capability requires RUN_BUDGET_MODE=enforce"
                )
        maximum_text = values.get("FILE_MAX_BYTES", str(128 * 1024 * 1024))
        try:
            maximum = int(maximum_text)
        except ValueError as exc:
            raise RunFileScopeUnavailable("FILE_MAX_BYTES must be an integer") from exc
        if maximum < 1 or maximum > 1024 * 1024 * 1024:
            raise RunFileScopeUnavailable("FILE_MAX_BYTES must be between 1 byte and 1 GiB")
        if not upload:
            return cls(False, transform, shell, None, None, maximum)
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
        return cls(True, transform, shell, store, run, maximum)
