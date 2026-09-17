from __future__ import annotations

from pathlib import Path

import pytest

from workspace.file_capability_config import FileCapabilityConfig
from workspace.file_scope import RunFileScopeUnavailable


def test_file_capability_defaults_closed(tmp_path: Path) -> None:
    config = FileCapabilityConfig.from_environment(tmp_path, None, {})
    assert config.upload_enabled is False
    assert config.transform_enabled is False
    assert config.shell_enabled is False
    assert config.store_root is None


def test_enabled_roots_are_pairwise_isolated(tmp_path: Path) -> None:
    workspace = tmp_path / "git"
    store = tmp_path / "objects"
    run = tmp_path / "runs"
    config = FileCapabilityConfig.from_environment(
        workspace,
        "postgresql://worker",
        {
            "WEB_FILE_UPLOAD_ENABLED": "true",
            "FILE_STORE_ROOT": str(store),
            "FILE_RUN_ROOT": str(run),
            "FILE_MAX_BYTES": "1024",
        },
    )
    assert config.store_root == store.resolve()
    assert config.run_root == run.resolve()
    assert config.max_bytes == 1024


@pytest.mark.parametrize("root_name", ["FILE_STORE_ROOT", "FILE_RUN_ROOT"])
def test_file_roots_cannot_overlap_git_workspace(
    tmp_path: Path, root_name: str,
) -> None:
    workspace = tmp_path / "git"
    environment = {
        "WEB_FILE_UPLOAD_ENABLED": "true",
        "FILE_STORE_ROOT": str(tmp_path / "objects"),
        "FILE_RUN_ROOT": str(tmp_path / "runs"),
    }
    environment[root_name] = str(workspace / "nested")
    with pytest.raises(RunFileScopeUnavailable, match="must not overlap"):
        FileCapabilityConfig.from_environment(
            workspace, "postgresql://worker", environment
        )


def test_file_store_and_run_root_cannot_overlap(tmp_path: Path) -> None:
    with pytest.raises(RunFileScopeUnavailable, match="must not overlap"):
        FileCapabilityConfig.from_environment(
            tmp_path / "git",
            "postgresql://worker",
            {
                "WEB_FILE_UPLOAD_ENABLED": "true",
                "FILE_STORE_ROOT": str(tmp_path / "files"),
                "FILE_RUN_ROOT": str(tmp_path / "files" / "runs"),
            },
        )


def test_production_requires_absolute_roots_and_enforced_budget(tmp_path: Path) -> None:
    base = {
        "HPAGENT_ENV": "production",
        "WEB_FILE_UPLOAD_ENABLED": "true",
        "FILE_STORE_ROOT": "relative-store",
        "FILE_RUN_ROOT": "relative-runs",
    }
    with pytest.raises(RunFileScopeUnavailable, match="RUN_BUDGET_MODE=enforce"):
        FileCapabilityConfig.from_environment(
            tmp_path, "postgresql://worker", base
        )
    base["RUN_BUDGET_MODE"] = "enforce"
    with pytest.raises(RunFileScopeUnavailable, match="absolute"):
        FileCapabilityConfig.from_environment(
            tmp_path, "postgresql://worker", base
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WEB_FILE_UPLOAD_ENABLED", "yes"),
        ("WEB_FILE_TRANSFORM_ENABLED", "1"),
        ("WEB_FILE_SHELL_ENABLED", "TRUE-ish"),
    ],
)
def test_feature_flags_reject_ambiguous_values(
    tmp_path: Path, name: str, value: str,
) -> None:
    with pytest.raises(RunFileScopeUnavailable, match="exactly true or false"):
        FileCapabilityConfig.from_environment(tmp_path, None, {name: value})
