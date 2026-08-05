from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1, max_length=4096)
    return_to: str = "/"


class CreateConversationRequest(StrictModel):
    title: str | None = None


class RenameConversationRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessageRequest(StrictModel):
    content: str


class EmptyRequest(StrictModel):
    pass
