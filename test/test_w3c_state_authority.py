"""W3-C removes the duplicate QQ conversation/session authority."""
from __future__ import annotations

import inspect
from pathlib import Path

from actions.runtime import ActionRuntime
from bootstrap.qq import build_qq_runtime
from orchestration.config import AgentConfig, AppConfig, WorkspaceConfig
from orchestration.worker import WorkerDependencies

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_state_authority_modules_are_absent() -> None:
    retired = (
        "src/session",
        "src/application/memory.py",
        "src/application/session_archive.py",
        "scripts/session-viewer.py",
        "scripts/migrate-memory-format.py",
    )
    assert [path for path in retired if (ROOT / path).exists()] == []


def test_composition_has_no_session_store_or_workspace_sqlite_authority() -> None:
    dependency_fields = WorkerDependencies.__dataclass_fields__
    assert "workspace_db" not in dependency_fields
    assert "file_store" not in dependency_fields
    assert "db_path" not in WorkspaceConfig.__dataclass_fields__
    assert "session" not in AppConfig.__dataclass_fields__
    assert "wal_enabled" not in AgentConfig.__dataclass_fields__
    assert "checkpoint_interval" not in AgentConfig.__dataclass_fields__
    assert "session_store" not in inspect.signature(ActionRuntime).parameters
    assert "file_store" not in inspect.signature(build_qq_runtime).parameters


def test_retained_state_systems_still_have_independent_owners() -> None:
    retained = (
        "src/storage/redis.py",
        "src/memory/hindsight_client.py",
        "src/workspace/isolation.py",
        "src/workspace/file_scope.py",
        "src/storage/tenant_file_store.py",
        "src/application/qq_delivery.py",
        "src/orchestration/document_workflow.py",
        "src/orchestration/artifact_workflow.py",
    )
    assert [path for path in retained if not (ROOT / path).is_file()] == []
