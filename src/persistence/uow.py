from __future__ import annotations

from contextlib import AbstractContextManager
from functools import wraps
from time import sleep
from typing import Any, Callable, ParamSpec, TypeVar

import psycopg

P = ParamSpec("P")
R = TypeVar("R")


def retryable_transaction(
    function: Callable[P, R], *, attempts: int = 3
) -> Callable[P, R]:
    """Retry the entire idempotent command after PostgreSQL concurrency aborts."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        for attempt in range(attempts):
            try:
                return function(*args, **kwargs)
            except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
                if attempt + 1 == attempts:
                    raise
                sleep(0.01 * (2**attempt))
        raise RuntimeError("unreachable")

    return wrapped


class UnitOfWork(AbstractContextManager["UnitOfWork"]):
    """Owns one short DB transaction; callers must not invoke external services in it."""

    def __init__(self, database_url: str):
        self.connection = psycopg.connect(database_url, row_factory=psycopg.rows.dict_row)
        try:
            self.connection.execute("SET search_path TO hpagent, public")
        except Exception:
            self.connection.close()
            raise

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        return self.connection.execute(query, params)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            try:
                self.connection.rollback()
            except Exception:
                # Cleanup must not replace the business exception already in flight.
                pass
            finally:
                try:
                    self.connection.close()
                except Exception:
                    pass
            return

        try:
            self.connection.commit()
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            try:
                self.connection.close()
            except Exception:
                pass
            raise
        self.connection.close()
