"""Keep Activity cancellation pending until an in-process file operation exits."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def run_blocking(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # asyncio.to_thread cannot be killed. Do not report a stopped Activity
        # or release its Run scope while its thread can still access bytes.
        try:
            await operation
        except Exception:
            pass
        raise
