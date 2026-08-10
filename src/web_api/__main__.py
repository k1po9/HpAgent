from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from common.logging import setup_logging

from .app import create_app


def main() -> None:
    setup_logging(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        log_dir=Path(os.getenv("LOG_DIR", ".data/logs")),
        json_filename="web-api.jsonl",
        error_log_filename="web-api-error.log",
    )
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
