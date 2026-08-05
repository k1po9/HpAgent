from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import psycopg


class UnitOfWork(AbstractContextManager["UnitOfWork"]):
    """Owns one short DB transaction; callers must not invoke external services in it."""

    def __init__(self, database_url: str):
        self.connection = psycopg.connect(database_url, row_factory=psycopg.rows.dict_row)
        self.connection.execute("SET search_path TO hpagent, public")

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        return self.connection.execute(query, params)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()
