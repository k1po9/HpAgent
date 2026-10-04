"""Publisher polling must use the real Outbox event validation boundary."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from web_api.terminal_publisher import TerminalEventPublisher
from web_domain.outbox import OutboxService


@pytest.mark.asyncio
async def test_publisher_recovers_and_claims_run_and_work_events(monkeypatch):
    # Replace only database I/O; keep both real Outbox validation methods.
    transaction = MagicMock()
    monkeypatch.setattr("web_domain.outbox.UnitOfWork", lambda _: transaction)
    events = [
        {"event_type": "publish_terminal_event"},
        {"event_type": "publish_work_event"},
    ]
    publisher = TerminalEventPublisher(object(), object(), SimpleNamespace())
    repository = Mock()
    repository.recover_expired.return_value = 0
    repository.claim_batch.return_value = events
    publisher.outbox.repository = repository
    published = []

    async def publish_one(event):
        published.append(event)
        if len(published) == len(events):
            publisher.redis = None

    monkeypatch.setattr(publisher, "_publish_one", publish_one)
    await asyncio.wait_for(publisher._run(), timeout=5)

    owned = frozenset({"publish_terminal_event", "publish_work_event"})
    uow = transaction.__enter__.return_value
    repository.recover_expired.assert_called_once_with(uow, 120, owned)
    repository.claim_batch.assert_called_once_with(uow, "terminal-publisher", owned, 10)
    assert published == events


@pytest.mark.parametrize("method", ["claim", "recover_expired"])
@pytest.mark.parametrize("event_types", [set(), {"publish_work_event", "unknown_event"}])
def test_outbox_rejects_empty_or_unknown_types_before_database_access(method, event_types):
    outbox = OutboxService(object())
    first_argument = "terminal-publisher" if method == "claim" else 120
    with pytest.raises(ValueError, match="known Outbox event types"):
        getattr(outbox, method)(first_argument, event_types)
