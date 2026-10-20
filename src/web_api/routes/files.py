from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.errors import CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation

from common.logging import log_event
from persistence.uow import UnitOfWork
from web_api.auth import AuthContext
from web_api.dependencies import auth_context, csrf_guard, file_service, idempotency_key
from web_api.models import (
    CreateUploadRequest,
    CreateWorkspaceDirectoryRequest,
    EmptyRequest,
    GrantConversationResourceRequest,
    MoveWorkspaceNodeRequest,
    SaveWorkspaceFileRequest,
    UpdateWorkspaceFileRequest,
)
from web_api.responses import error_response
from web_domain.errors import ResourceNotFound
from web_domain.file_services import FileService
from workspace.catalog import WorkspaceConflict, WorkspaceVersionConflict
from workspace.discovery import WorkspaceDiscovery
from workspace.resources import ResourcePolicy

logger = logging.getLogger("HpAgent.WebApi")

router = APIRouter()


def workspace_mutation(function: Callable[[], Any]) -> Any:
    try:
        return function()
    except (CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation) as exc:
        reasons = {
            "uq_workspace_nodes__siblings": "name_exists",
            "fk_workspace_nodes__parent": "invalid_parent",
            "ck_workspace_nodes__name": "invalid_name",
            "ck_workspace_nodes__root": "immutable_node",
            "Parent must be an active directory in this Workspace": "invalid_parent",
            "Directory is not empty": "directory_not_empty",
            "Workspace cycle": "workspace_cycle",
            "Workspace root is immutable": "immutable_node",
            "Workspace node identity is immutable": "immutable_node",
            "Workspace content identity is immutable": "immutable_node",
            "Workspace tombstone cannot be revived": "immutable_node",
            "Workspace file is unavailable": "file_unavailable",
            "Workspace unavailable": "invalid_parent",
        }
        reason = reasons.get(exc.diag.constraint_name or "") or reasons.get(
            exc.diag.message_primary or ""
        )
        if reason is None:
            raise
        raise WorkspaceConflict(reason, reason=reason) from exc


@router.get("/api/v1/conversations/{conversation_id}/file-candidates")
def list_file_candidates(
    conversation_id: UUID,
    request: Request,
    before: UUID | None = None,
    context: AuthContext = Depends(auth_context),
    files: FileService = Depends(file_service),
):
    return files.list_candidates(context.account_id, conversation_id, before)


@router.post("/api/v1/conversations/{conversation_id}/uploads")
def create_upload(
    conversation_id: UUID,
    payload: CreateUploadRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
    files: FileService = Depends(file_service),
):
    result = files.create_upload(
        context.account_id,
        conversation_id,
        key,
        payload.file_name,
        payload.size_bytes,
        payload.content_type,
        payload.sha256,
    )
    response = JSONResponse(status_code=result.response_status, content=result.body)
    response.headers["Location"] = result.body["content_url"]
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.post("/api/v1/workspace/uploads")
def create_workspace_upload(
    payload: CreateUploadRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
    files: FileService = Depends(file_service),
):
    result = files.create_workspace_upload(
        context.account_id,
        key,
        payload.file_name,
        payload.size_bytes,
        payload.content_type,
        payload.sha256,
    )
    response = JSONResponse(status_code=result.response_status, content=result.body)
    response.headers["Location"] = result.body["content_url"]
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.put("/api/v1/uploads/{file_id}/content")
async def upload_content(
    file_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    files: FileService = Depends(file_service),
):
    uploaded = await files.upload_content(context.account_id, file_id, request.stream())
    return {"file": uploaded}


@router.get("/api/v1/files/{file_id}")
def get_file(
    file_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
    files: FileService = Depends(file_service),
):
    return {"file": files.get(context.account_id, file_id)}


@router.get("/api/v1/workspace")
def get_workspace(request: Request, context: AuthContext = Depends(auth_context)):
    return request.app.state.workspace.tree(context.account_id)


@router.get("/api/v1/workspace/search")
def search_workspace(
    request: Request,
    context: AuthContext = Depends(auth_context),
    name: str | None = Query(default=None, max_length=255),
    summary: str | None = Query(default=None, max_length=255),
    content_type: str | None = None,
    purpose: str | None = None,
    work_id: UUID | None = None,
    source_run_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    after: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return WorkspaceDiscovery(request.app.state.api_pool).search(
        context.account_id,
        name=name,
        summary=summary,
        content_type=content_type,
        purpose=purpose,
        work_id=work_id,
        source_run_id=source_run_id,
        from_date=from_date,
        to_date=to_date,
        after=after,
        limit=limit,
    )


@router.get("/api/v1/runs/{run_id}/workspace/search")
def search_run_workspace(
    run_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
    name: str | None = Query(default=None, max_length=255),
    summary: str | None = Query(default=None, max_length=255),
    content_type: str | None = None,
    purpose: str | None = None,
    work_id: UUID | None = None,
    source_run_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    after: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return WorkspaceDiscovery(request.app.state.api_pool).search(
        context.account_id,
        run_id=run_id,
        name=name,
        summary=summary,
        content_type=content_type,
        purpose=purpose,
        work_id=work_id,
        source_run_id=source_run_id,
        from_date=from_date,
        to_date=to_date,
        after=after,
        limit=limit,
    )


@router.get("/api/v1/workspace/space")
def workspace_space(request: Request, context: AuthContext = Depends(auth_context)):
    return WorkspaceDiscovery(request.app.state.api_pool).space(context.account_id)


@router.get("/api/v1/workspace/nodes/{node_id}/trace")
def workspace_trace(node_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return WorkspaceDiscovery(request.app.state.api_pool).trace(context.account_id, node_id)


@router.get("/api/v1/workspace/files/{file_id}/retention")
def workspace_retention(
    file_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    return WorkspaceDiscovery(request.app.state.api_pool).retention(context.account_id, file_id)


@router.get("/api/v1/conversations/{conversation_id}/resources")
def conversation_resources(
    conversation_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    policy: ResourcePolicy = request.app.state.resource_policy
    return {
        "grants": policy.grants(context.account_id, "conversation", conversation_id),
        "attachments": policy.conversation_files(context.account_id, conversation_id),
    }


@router.post("/api/v1/conversations/{conversation_id}/resources", status_code=201)
def grant_conversation_resource(
    conversation_id: UUID,
    payload: GrantConversationResourceRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
):
    ids = request.app.state.resource_policy.grant(
        context.account_id,
        "conversation",
        conversation_id,
        payload.node_id,
        payload.operations,
        payload.recursive,
    )
    return {"grant_ids": ids}


@router.delete("/api/v1/conversations/{conversation_id}/resources/{grant_id}")
def revoke_conversation_resource(
    conversation_id: UUID,
    grant_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
):
    policy: ResourcePolicy = request.app.state.resource_policy
    with UnitOfWork(request.app.state.api_pool) as uow:
        if (
            uow.execute(
                "SELECT 1 FROM resource_grants WHERE account_id=%s AND grant_id=%s "
                "AND subject_kind='conversation' AND subject_id=%s",
                (context.account_id, grant_id, conversation_id),
            ).fetchone()
            is None
        ):
            raise ResourceNotFound()
    affected = policy.revoke(context.account_id, grant_id, request.app.state.commands)
    with UnitOfWork(request.app.state.api_pool) as uow:
        statuses = {
            row["run_id"]: row["status"]
            for row in uow.execute(
                "SELECT run_id,status FROM runs WHERE account_id=%s AND run_id=ANY(%s)",
                (context.account_id, affected),
            ).fetchall()
        }
    for run_id in affected:
        if statuses.get(run_id) != "cancelled":
            log_event(
                logger,
                logging.WARNING,
                "workspace_stop_unconfirmed",
                "web_api",
                run_id=str(run_id),
                grant_id=str(grant_id),
            )
    return {
        "affected_runs": [
            {
                "run_id": str(run_id),
                "stop_state": "stopped" if statuses.get(run_id) == "cancelled" else "stopping",
            }
            for run_id in affected
        ]
    }


@router.delete("/api/v1/conversations/{conversation_id}/attachments/{file_id}")
def revoke_conversation_attachment(
    conversation_id: UUID,
    file_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
):
    affected = request.app.state.resource_policy.revoke_conversation_file(
        context.account_id, conversation_id, file_id, request.app.state.commands
    )
    return {
        "affected_runs": [{"run_id": str(run_id), "stop_state": "stopping"} for run_id in affected]
    }


@router.get("/api/v1/runs/{run_id}/resources")
def run_resources(
    run_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
    after: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=100),
):
    return request.app.state.resource_policy.candidates(context.account_id, run_id, after, limit)


@router.post("/api/v1/workspace/directories", status_code=201)
def create_workspace_directory(
    payload: CreateWorkspaceDirectoryRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
):
    node_id = workspace_mutation(
        lambda: request.app.state.workspace.create_directory(
            context.account_id, payload.parent_id, payload.name
        )
    )
    return {"node_id": str(node_id)}


@router.post("/api/v1/workspace/files", status_code=201)
def save_workspace_file(
    payload: SaveWorkspaceFileRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    node_id = workspace_mutation(
        lambda: request.app.state.workspace.save_file(
            context.account_id, payload.parent_id, payload.file_id, payload.name, key
        )
    )
    return {"node_id": str(node_id)}


@router.get("/api/v1/workspace/nodes/{node_id}/versions")
def workspace_versions(
    node_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    return request.app.state.workspace.versions(context.account_id, node_id)


@router.get("/api/v1/runs/{run_id}/published-files")
def run_published_files(
    run_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    with UnitOfWork(request.app.state.api_pool) as uow:
        if (
            uow.execute(
                "SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s", (context.account_id, run_id)
            ).fetchone()
            is None
        ):
            raise ResourceNotFound()
        rows = uow.execute(
            "SELECT rf.file_id,sf.display_name,sf.sha256 FROM run_files rf "
            "JOIN stored_files sf ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
            "WHERE rf.account_id=%s AND rf.run_id=%s AND rf.direction='output' "
            "AND sf.status='ready' AND sf.source_run_id=%s ORDER BY rf.file_id",
            (context.account_id, run_id, run_id),
        ).fetchall()
    return {
        "files": [
            {"file_id": str(r["file_id"]), "name": r["display_name"], "sha256": r["sha256"]}
            for r in rows
        ]
    }


@router.post("/api/v1/workspace/nodes/{node_id}/upgrade")
def upgrade_workspace_file(
    node_id: UUID, request: Request, context: AuthContext = Depends(csrf_guard)
):
    return workspace_mutation(
        lambda: request.app.state.workspace.upgrade_file(context.account_id, node_id)
    )


@router.post("/api/v1/workspace/nodes/{node_id}/versions", status_code=201)
def update_workspace_file(
    node_id: UUID,
    payload: UpdateWorkspaceFileRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    try:
        return workspace_mutation(
            lambda: request.app.state.workspace.update_file(
                context.account_id,
                node_id,
                payload.run_id,
                payload.file_id,
                payload.expected_revision,
                payload.expected_sha256,
                key,
            )
        )
    except WorkspaceVersionConflict as exc:
        log_event(
            logger,
            logging.WARNING,
            "workspace_cas_conflict",
            "web_api",
            node_id=str(node_id),
            run_id=str(payload.run_id),
            file_id=str(payload.file_id),
        )
        return error_response(
            request,
            409,
            "workspace_version_conflict",
            "Workspace 版本已更新，已发布输出可另存。",
            details={"current": exc.current, "published_file_id": str(payload.file_id)},
        )


@router.get("/api/v1/workspace/nodes/{node_id}/impact")
def workspace_node_impact(
    node_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    return request.app.state.workspace.preview_change(context.account_id, node_id)


@router.patch("/api/v1/workspace/nodes/{node_id}")
def move_workspace_node(
    node_id: UUID,
    payload: MoveWorkspaceNodeRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
):
    workspace_mutation(
        lambda: request.app.state.workspace.move(
            context.account_id, node_id, payload.parent_id, payload.name, payload.preview_token
        )
    )
    return {"node_id": str(node_id)}


@router.delete("/api/v1/workspace/nodes/{node_id}", status_code=204)
def remove_workspace_node(
    node_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
):
    preview_token = request.headers.get("X-Workspace-Preview")
    if not preview_token:
        raise HTTPException(status_code=422, detail="Workspace impact preview is required")
    workspace_mutation(
        lambda: request.app.state.workspace.remove(context.account_id, node_id, preview_token)
    )
    return Response(status_code=204)


@router.delete("/api/v1/files/{file_id}", status_code=204)
def delete_file(
    file_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    files: FileService = Depends(file_service),
):
    files.delete(context.account_id, file_id)
    return Response(status_code=204)


@router.get("/api/v1/files/{file_id}/lineage")
def get_file_lineage(
    file_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
    files: FileService = Depends(file_service),
):
    return {"files": files.lineage(context.account_id, file_id)}


@router.get("/api/v1/runs/{run_id}/file-action-approvals")
def list_file_action_approvals(
    run_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
):
    return {"approvals": request.app.state.file_approvals.list_for_run(context.account_id, run_id)}


@router.post("/api/v1/file-action-approvals/{approval_id}/approve")
def approve_file_action(
    approval_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    del payload
    result = request.app.state.file_approvals.decide(
        context.account_id, approval_id, "approved", key
    )
    response = JSONResponse(status_code=result.response_status, content=result.body)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.post("/api/v1/file-action-approvals/{approval_id}/reject")
def reject_file_action(
    approval_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    del payload
    result = request.app.state.file_approvals.decide(
        context.account_id, approval_id, "rejected", key
    )
    response = JSONResponse(status_code=result.response_status, content=result.body)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.get("/api/v1/files/{file_id}/content")
def download_file(
    file_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
    files: FileService = Depends(file_service),
):
    metadata, stream = files.download(context.account_id, file_id)

    def body():
        try:
            while chunk := stream.read(64 * 1024):
                yield chunk
        finally:
            stream.close()

    encoded_name = quote(str(metadata["file_name"]), safe="")
    headers = {
        "Content-Disposition": (
            f"attachment; filename=download.txt; filename*=UTF-8''{encoded_name}"
        )
    }
    return StreamingResponse(body(), media_type="application/octet-stream", headers=headers)
