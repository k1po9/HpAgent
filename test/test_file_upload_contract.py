from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from web_api.models import CreateUploadRequest, SendMessageRequest
from web_domain.errors import FileUploadInvalid, UnsupportedFileType
from web_domain.file_services import FileService


def test_upload_request_requires_lowercase_sha256() -> None:
    valid = CreateUploadRequest(
        file_name="service.log", size_bytes=3, content_type="text/plain",
        sha256="a" * 64,
    )
    assert valid.sha256 == "a" * 64
    with pytest.raises(ValidationError):
        CreateUploadRequest(
            file_name="service.log", size_bytes=3, content_type="text/plain",
            sha256="A" * 64,
        )


def test_message_attachment_contract_uses_uuid_and_has_hard_count_limit() -> None:
    request = SendMessageRequest(content="inspect", file_ids=[uuid4()])
    assert len(request.file_ids) == 1
    with pytest.raises(ValidationError):
        SendMessageRequest(content="inspect", file_ids=[uuid4() for _ in range(21)])


def test_display_name_never_preserves_a_client_path() -> None:
    original, display = FileService._names(r"C:\fakepath\service.log")
    assert original == r"C:\fakepath\service.log"
    assert display == "service.log"
    with pytest.raises(FileUploadInvalid):
        FileService._names("../")


@pytest.mark.parametrize(("declared", "expected"), [
    ("text/markdown", "text/markdown"),
    ("text/markdown; charset=utf-8", "text/markdown"),
    ("text/x-markdown", "text/markdown"),
    ("", "text/markdown"),
    ("text/plain", "text/plain"),
])
def test_markdown_declared_type_compatibility(declared: str, expected: str) -> None:
    assert FileService._declared_type(declared, "笔记.MD") == expected
    assert FileService._requires_utf8(expected, "笔记.MD") is True


def test_other_file_types_keep_mime_restrictions() -> None:
    assert FileService._declared_type("text/plain", "note.txt") == "text/plain"
    with pytest.raises(UnsupportedFileType):
        FileService._declared_type("text/x-markdown", "note.exe")
    with pytest.raises(UnsupportedFileType):
        FileService._declared_type("", "note.exe")
    with pytest.raises(UnsupportedFileType):
        FileService._declared_type("application/x-msdownload", "note.md")
