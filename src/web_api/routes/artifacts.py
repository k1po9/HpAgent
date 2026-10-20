from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from persistence.command_result import CommandResult
from web_api.auth import AuthContext
from web_api.dependencies import auth_context, csrf_guard, idempotency_key
from web_api.models import CreateArtifactRequest, CreateArtifactVersionRequest
from web_api.responses import error_response

router = APIRouter()


@router.post("/api/v1/messages/{message_id}/artifacts")
def create_artifact(
    message_id: UUID,
    payload: CreateArtifactRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    try:
        result: CommandResult = request.app.state.artifacts.create_artifact(
            context.account_id, message_id, key, payload.instruction
        )
    except ValueError as exc:
        if str(exc) == "artifact_source_invalid":
            return error_response(
                request,
                409,
                "artifact_source_invalid",
                "只能从已完成的 Assistant 消息生成 Artifact。",
            )
        raise
    response = JSONResponse(status_code=result.response_status, content=result.body)
    response.headers["Location"] = f"/api/v1/artifacts/{result.body['artifact']['artifact_id']}"
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.get("/api/v1/messages/{message_id}/artifacts")
def list_message_artifacts(
    message_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    return request.app.state.artifacts.list_for_message(context.account_id, message_id)


@router.get("/api/v1/artifacts/{artifact_id}")
def get_artifact(artifact_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return request.app.state.artifacts.get_artifact(context.account_id, artifact_id)


@router.get("/api/v1/artifacts/{artifact_id}/versions")
def list_artifact_versions(
    artifact_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    return request.app.state.artifacts.list_versions(context.account_id, artifact_id)


@router.post("/api/v1/artifacts/{artifact_id}/versions")
def create_artifact_version(
    artifact_id: UUID,
    payload: CreateArtifactVersionRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    try:
        result: CommandResult = request.app.state.artifacts.create_version(
            context.account_id, artifact_id, key, payload.instruction
        )
    except ValueError as exc:
        if str(exc) == "artifact_instruction_required":
            return error_response(request, 422, "validation_error", "修改要求不能为空。")
        if str(exc) == "artifact_research_version_unsupported":
            return error_response(
                request,
                409,
                "artifact_research_version_unsupported",
                "Research Artifact 当前只能由 Research Workflow 生成版本。",
            )
        raise
    response = JSONResponse(status_code=result.response_status, content=result.body)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.get("/api/v1/artifact-versions/{artifact_version_id}")
def get_artifact_version(
    artifact_version_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    return request.app.state.artifacts.get_version(context.account_id, artifact_version_id)
