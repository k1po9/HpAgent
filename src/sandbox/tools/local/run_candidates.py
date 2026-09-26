"""Bounded discovery and explicit selection of frozen Run candidates."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class CandidatePageInput(BaseModel):
    after: str | None = None
    limit: int = Field(default=50, ge=1, le=100)


class CandidateSelectInput(BaseModel):
    node_id: UUID


def create_run_candidate_tools(scope_provider: Callable[[], Any | None]) -> list[StructuredTool]:
    def scope() -> Any:
        value = scope_provider()
        if value is None:
            raise ValueError("Run file scope is unavailable")
        return value

    async def list_candidates(after: str | None = None, limit: int = 50) -> str:
        return json.dumps(scope().candidates(after, limit), ensure_ascii=False)

    async def select_candidate(node_id: UUID) -> str:
        return json.dumps(scope().select(node_id), ensure_ascii=False)

    tools = [
        StructuredTool.from_function(
            name="list_run_candidates", coroutine=list_candidates,
            args_schema=CandidatePageInput,
            description="List one page of metadata from this Run's frozen Workspace candidates.",
        ),
        StructuredTool.from_function(
            name="select_run_candidate", coroutine=select_candidate,
            args_schema=CandidateSelectInput,
            description="Fix and make available one authorized Workspace candidate for this Run.",
        ),
    ]
    for tool in tools:
        tool.metadata = {"side_effect_class": "read_only", "file_scope_required": True}
    return tools
