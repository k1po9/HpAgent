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
    invite_code: str | None = Field(default=None, min_length=1, max_length=512)


class CreateConversationRequest(StrictModel):
    title: str | None = None


class CreateWorkRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    requirement: dict
    conversation_id: UUID | None = None
    source_message_id: UUID | None = None


class ReviseWorkRequest(StrictModel):
    requirement: dict
    change_reason: str = Field(default="user_revision", min_length=1, max_length=500)


class RenameConversationRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessageRequest(StrictModel):
    content: str
    agent_strategy: Literal["react", "plan_and_execute"] = "react"
    file_ids: list[UUID] = Field(default_factory=list, max_length=20)


class CreateUploadRequest(StrictModel):
    file_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    content_type: str = Field(max_length=255)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EmptyRequest(StrictModel):
    pass


class CreateWorkspaceDirectoryRequest(StrictModel):
    parent_id: UUID
    name: str = Field(min_length=1, max_length=255)


class SaveWorkspaceFileRequest(StrictModel):
    parent_id: UUID
    file_id: UUID
    name: str = Field(min_length=1, max_length=255)


class UpdateWorkspaceFileRequest(StrictModel):
    run_id: UUID
    file_id: UUID
    expected_revision: int = Field(ge=1)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MoveWorkspaceNodeRequest(StrictModel):
    parent_id: UUID
    name: str = Field(min_length=1, max_length=255)
    preview_token: str = Field(pattern=r"^[0-9a-f]{64}$")


class GrantConversationResourceRequest(StrictModel):
    node_id: UUID
    operations: list[Literal["list_metadata", "read_content", "create_child",
                             "update_content", "delete_entry"]] = Field(min_length=1, max_length=5)
    recursive: bool = False


class CreateArtifactRequest(StrictModel):
    instruction: str | None = Field(default=None, max_length=4000)


class CreateArtifactVersionRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=4000)


class WorkBudgetRequest(StrictModel):
    budget_version: int = Field(ge=1)
    limits: dict[str, int]


class WorkInputRequest(StrictModel):
    file_id: UUID
    source_message_id: UUID | None = None
    purpose: str = Field(min_length=1, max_length=200)


class WorkTargetRequest(StrictModel):
    source_message_id: UUID | None = None
    content_scope: Literal['summary','content'] = 'summary'


class ReferenceWorkArtifactRequest(StrictModel):
    artifact_version_id: UUID
    role: Literal['input','evidence']


class AcceptWorkResultRequest(StrictModel):
    requirement_revision: int = Field(ge=1)
    artifact_version_id: UUID


class ResolveDeliveryRequest(StrictModel):
    outcome: Literal['accepted','not_sent','retry_accepting_duplicate_risk']
