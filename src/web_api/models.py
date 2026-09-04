from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1, max_length=4096)
    return_to: str = "/"


class RegisterRequest(StrictModel):
    username: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1, max_length=128)


class CreateConversationRequest(StrictModel):
    title: str | None = None


class SourceStrategyRequest(StrictModel):
    public_web: bool = True
    official_sources: bool = True
    github: bool = True
    rss: bool = True
    uploaded_files: bool = False
    freshness_days: int = Field(default=7, ge=0, le=3650)
    preferred_domains: list[str] = Field(default_factory=list, max_length=100)
    rss_feeds: list[str] = Field(default_factory=list, max_length=100)


class CreateResearchTaskRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20000)
    conversation_id: UUID | None = None
    source_strategy: SourceStrategyRequest = Field(default_factory=SourceStrategyRequest)


class UpdateResearchScheduleRequest(StrictModel):
    schedule_type: Literal["manual", "daily"]
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    expression: str | None = Field(default=None, max_length=20)
    enabled: bool = False


class RenameConversationRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessageRequest(StrictModel):
    content: str
    agent_strategy: Literal["react", "plan_and_execute"] = "react"
    file_ids: list[UUID] = Field(default_factory=list, max_length=20)


class CreateUploadRequest(StrictModel):
    file_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    content_type: str = Field(min_length=1, max_length=255)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EmptyRequest(StrictModel):
    pass


class CreateArtifactRequest(StrictModel):
    instruction: str | None = Field(default=None, max_length=4000)


class CreateArtifactVersionRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=4000)
