"""Gotenberg ConversionProvider for Office-to-PDF conversion."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from file_domain.models import FileResource


class GotenbergConversionProvider:
    def __init__(
        self, base_url: str, *, timeout_seconds: float = 120.0,
        max_output_bytes: int = 128 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.transport = transport

    def convert_to_pdf(
        self, resource: FileResource, outputs_root: Path, logical_name: str
    ) -> Path:
        if resource.local_path.suffix.casefold() not in {".docx", ".xlsx", ".pptx"}:
            raise ValueError("Gotenberg P0 conversion accepts DOCX, XLSX, or PPTX")
        if not logical_name.casefold().endswith(".pdf"):
            raise ValueError("converted output name must end with .pdf")
        target = (outputs_root / logical_name).absolute()
        temporary = outputs_root / f".{logical_name}.part"
        if target.parent != outputs_root or target.exists() or temporary.exists():
            raise ValueError("invalid or existing PDF output path")
        try:
            with resource.local_path.open("rb") as source, httpx.Client(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client, client.stream(
                "POST",
                f"{self.base_url}/forms/libreoffice/convert",
                files={"files": (resource.logical_name, source, resource.media_type)},
            ) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and int(declared) > self.max_output_bytes:
                    raise ValueError("converted PDF exceeds output byte limit")
                total = 0
                with temporary.open("xb") as destination:
                    for chunk in response.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > self.max_output_bytes:
                            raise ValueError("converted PDF exceeds output byte limit")
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
            os.replace(temporary, target)
            return target
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
