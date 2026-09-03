from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from file_runtime import FileResourceResolver
from storage.tenant_file_store import TenantFileStore
from workspace.file_scope import RunFileScopeUnavailable, RunFileWorkspace


def _published(store: TenantFileStore, account_id, file_id, content: bytes):
    staged = store.stage(file_id, [content], declared_size=len(content))
    return store.publish(account_id, file_id, staged)


def test_run_scope_materializes_read_only_inputs_and_cleans_after_exit(tmp_path: Path) -> None:
    account_id, file_id, run_id = uuid4(), uuid4(), uuid4()
    store = TenantFileStore(tmp_path / "objects", max_bytes=1024)
    published = _published(store, account_id, file_id, b"ERROR one\n")
    workspace = RunFileWorkspace(object(), store, tmp_path / "executions")
    rows = [{
        "file_id": file_id, "logical_name": "service.log",
        "storage_key": published.storage_key, "size_bytes": published.size_bytes,
        "encoding": "utf-8",
    }]

    with workspace.prepare_rows(run_id, rows) as scope:
        input_path = scope.inputs_root / "service.log"
        assert input_path.read_bytes() == b"ERROR one\n"
        assert input_path.stat().st_mode & 0o777 == 0o400
        assert scope.scratch_root.stat().st_mode & 0o777 == 0o700
        assert scope.outputs_root.stat().st_mode & 0o777 == 0o700
        assert scope.model_manifest() == [{
            "file": "service.log", "file_id_suffix": str(file_id)[-8:],
            "size_bytes": len(b"ERROR one\n"), "encoding": "utf-8",
        }]
    assert not (workspace.execution_root / str(run_id)).exists()


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "nested/file", "..", ""])
def test_run_scope_rejects_unsafe_logical_names(tmp_path: Path, name: str) -> None:
    store = TenantFileStore(tmp_path / "objects", max_bytes=10)
    workspace = RunFileWorkspace(object(), store, tmp_path / "executions")
    with pytest.raises(RunFileScopeUnavailable):
        with workspace.prepare_rows(uuid4(), [{
            "file_id": uuid4(), "logical_name": name, "storage_key": "unused",
            "size_bytes": 0, "encoding": "utf-8",
        }]):
            pass


def test_execution_and_object_roots_must_not_overlap(tmp_path: Path) -> None:
    store = TenantFileStore(tmp_path / "files", max_bytes=10)
    with pytest.raises(RunFileScopeUnavailable):
        RunFileWorkspace(object(), store, store.root / "executions")


def test_run_scope_rematerializes_prior_output_for_next_version(tmp_path: Path) -> None:
    account_id, file_id, run_id = uuid4(), uuid4(), uuid4()
    store = TenantFileStore(tmp_path / "objects", max_bytes=1024)
    published = _published(store, account_id, file_id, b"prior output")
    workspace = RunFileWorkspace(object(), store, tmp_path / "executions")
    rows = [{
        "file_id": file_id, "logical_name": "report-v2.docx", "direction": "output",
        "storage_key": published.storage_key, "size_bytes": published.size_bytes,
        "encoding": "binary", "content_type": "application/docx",
    }]
    with workspace.prepare_rows(run_id, rows) as scope:
        resource = FileResourceResolver(scope).resolve(str(file_id))
        assert resource.local_path == scope.outputs_root / "report-v2.docx"
        assert resource.local_path.read_bytes() == b"prior output"
        assert resource.local_path.stat().st_mode & 0o777 == 0o400
        assert scope.model_manifest()[0]["direction"] == "output"
