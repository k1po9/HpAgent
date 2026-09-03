from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from document_activities import DocumentActivities, NormalizeDocumentInput
from file_domain.models import (
    FileResource,
    NormalizedBlock,
    NormalizedDocument,
    SourceLocator,
)
from file_domain.serialization import normalized_document_to_dict
from orchestration.document_contracts import DOCUMENT_TASK_QUEUE
from orchestration.document_worker import build_document_worker
from workspace.file_scope import RunFileInput, RunFileScope


def _document(tmp_path: Path) -> NormalizedDocument:
    file_id = uuid4()
    resource = FileResource(
        file_id, "private.pdf", tmp_path / "secret" / "private.pdf", 123,
        "application/pdf", sha256="a" * 64,
    )
    locator = SourceLocator(file_id, "private.pdf", page=2, bbox=(1, 2, 3, 4))
    return NormalizedDocument(
        resource,
        (NormalizedBlock("paragraph", "evidence", locator),),
        metadata={"adapter": "docling", "truncated": False},
    )


def test_persisted_document_contract_excludes_worker_path(tmp_path: Path) -> None:
    payload = normalized_document_to_dict(_document(tmp_path))
    assert payload["resource"]["logical_name"] == "private.pdf"
    assert "local_path" not in payload["resource"]
    assert str(tmp_path) not in str(payload)
    assert payload["blocks"][0]["source_locator"]["bbox"] == [1, 2, 3, 4]


async def test_document_activity_returns_only_compact_persisted_ref(tmp_path: Path) -> None:
    document = _document(tmp_path)
    request = NormalizeDocumentInput(
        1, str(uuid4()), str(uuid4()), str(document.resource.file_id), "doc-op-1"
    )
    activities = DocumentActivities(object(), object(), object())
    activities._find = lambda operation_id: None
    activities._normalize = lambda value: (normalized_document_to_dict(document), 123)
    activities._input_size = lambda value: 123
    activities._trace_parent = lambda run_id: uuid4()
    captured: dict = {}

    def persist(value, document_ref, payload, block_count, table_count, truncated):
        captured["payload"] = payload
        return {
            "document_ref": document_ref,
            "run_id": request.run_id,
            "file_id": request.file_id,
            "block_count": block_count,
            "table_count": table_count,
            "truncated": truncated,
        }

    activities._persist = persist
    activities.budget = SimpleNamespace(reserve=lambda *args: None, settle=lambda *args: None)
    activities.trace = SimpleNamespace(
        start_event=lambda *args: None, finish_event=lambda *args: None
    )
    result = await activities.normalize_document(request)

    assert result == {
        "schema_version": 1,
        "run_id": request.run_id,
        "file_id": request.file_id,
        "document_ref": f"normalized-document:{request.run_id}:{request.file_id}",
        "block_count": 1,
        "table_count": 0,
        "truncated": False,
    }
    assert "evidence" in str(captured["payload"])
    assert "blocks" not in result


async def test_document_activity_replay_skips_parser_and_budget() -> None:
    request = NormalizeDocumentInput(1, str(uuid4()), str(uuid4()), str(uuid4()), "op")
    existing = {
        "document_ref": "normalized-document:existing",
        "run_id": request.run_id,
        "file_id": request.file_id,
        "block_count": 3,
        "table_count": 1,
        "truncated": True,
    }
    activities = DocumentActivities(object(), object(), object())
    activities._find = lambda operation_id: existing
    activities._ledger_state = lambda run_id, operation_id: None
    activities._normalize = lambda value: (_ for _ in ()).throw(AssertionError("parsed twice"))
    activities.budget = SimpleNamespace(
        reserve=lambda *args: (_ for _ in ()).throw(AssertionError("reserved twice"))
    )
    assert await activities.normalize_document(request) == {
        "schema_version": 1,
        **existing,
    }


def test_document_worker_is_pinned_to_queue_and_concurrency_one(monkeypatch) -> None:
    captured = {}

    def fake_worker(client, **kwargs):
        captured.update(kwargs)
        return "worker"

    monkeypatch.setattr("orchestration.document_worker.Worker", fake_worker)
    activities = SimpleNamespace(normalize_document=object())
    assert build_document_worker(object(), activities) == "worker"
    assert captured["task_queue"] == DOCUMENT_TASK_QUEUE
    assert captured["max_concurrent_activities"] == 1
    assert captured["activities"] == [activities.normalize_document]


def test_activity_resolves_only_the_authoritative_run_file(tmp_path: Path) -> None:
    document = _document(tmp_path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    path = inputs / "private.pdf"
    path.write_bytes(b"pdf")
    item = RunFileInput(
        document.resource.file_id, "private.pdf", 3, "binary", "application/pdf"
    )
    scope = RunFileScope(uuid4(), inputs, tmp_path / "scratch", tmp_path / "outputs", (item,))

    class Workspace:
        @contextmanager
        def prepare(self, account_id, run_id):
            yield scope

    provider = SimpleNamespace(parse=lambda resource: document)
    activities = DocumentActivities(object(), Workspace(), provider)
    request = NormalizeDocumentInput(
        1, str(uuid4()), str(scope.run_id), str(item.file_id), "op"
    )
    payload, scanned = activities._normalize(request)
    assert scanned == 3
    assert payload["resource"]["file_id"] == str(item.file_id)
