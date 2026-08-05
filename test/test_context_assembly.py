from __future__ import annotations

import pytest

from application.context_assembly import (
    ContextAssemblyService,
    ContextIsolationError,
    WebContextBase,
)
from harness.context_builder import HarnessContextBuilder
from memory.hindsight_client import MemoryItem


class FailingRecall:
    async def recall(self, *args, **kwargs):
        raise OSError("temporarily unavailable")


class CrossAccountRecall:
    async def recall(self, *args, **kwargs):
        item = MemoryItem(content="must not enter prompt")
        item.account_id = "other-account"
        return [item]


@pytest.mark.asyncio
async def test_recall_failure_degrades_to_empty_memory():
    service = ContextAssemblyService("unused", HarnessContextBuilder(), FailingRecall())
    base = WebContextBase(
        run_id=__import__("uuid").uuid4(), account_id=__import__("uuid").uuid4(),
        conversation_id=__import__("uuid").uuid4(), session_id=__import__("uuid").uuid4(),
        context_message_seq=1, trigger_message_id=__import__("uuid").uuid4(), trigger_content="hi",
        short_term_events=(),
    )
    assert await service.recall_long_term(base, "rewritten query") == ()


@pytest.mark.asyncio
async def test_cross_account_recall_is_a_safe_failure():
    service = ContextAssemblyService("unused", HarnessContextBuilder(), CrossAccountRecall())
    base = WebContextBase(
        run_id=__import__("uuid").uuid4(), account_id=__import__("uuid").uuid4(),
        conversation_id=__import__("uuid").uuid4(), session_id=__import__("uuid").uuid4(),
        context_message_seq=1, trigger_message_id=__import__("uuid").uuid4(), trigger_content="hi",
        short_term_events=(),
    )
    with pytest.raises(ContextIsolationError):
        await service.recall_long_term(base, "rewritten query")
