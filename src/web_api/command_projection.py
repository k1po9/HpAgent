"""HTTP links are projections of committed Conversation command results."""
from copy import deepcopy
from typing import Any

from persistence.command_result import CommandResult


def command_body(result: CommandResult, *fields: str, events: bool = False) -> dict[str, Any]:
    body = deepcopy({name: result.body[name] for name in fields})
    for name in ("user_message", "assistant_message"):
        if name in body:
            for file in body[name].get("files", []):
                file["download_url"] = (
                    f"/api/v1/files/{file['file_id']}/content"
                    if file["status"] == "ready" else None
                )
    if events:
        body["events_url"] = f"/api/v1/runs/{result.body['run']['run_id']}/events"
    return body
