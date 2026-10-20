from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from persistence.uow import UnitOfWork
from web_api.auth import AuthContext
from web_api.dependencies import Unauthenticated, auth_context, csrf_guard, idempotency_key
from web_api.models import (
    AcceptWorkResultRequest,
    CreateWorkRequest,
    EmptyRequest,
    GrantConversationResourceRequest,
    ReferenceWorkArtifactRequest,
    ResolveDeliveryRequest,
    ReviseWorkRequest,
    WorkBudgetRequest,
    WorkInputRequest,
    WorkTargetRequest,
)
from work_domain.models import Requirement

router = APIRouter()


def work_response(result):
    response = JSONResponse(status_code=result.response_status, content=result.body)
    response.headers["ETag"] = (
        f'"work-{result.body["work"]["work_id"]}-v{result.body["work"]["row_version"]}"'
    )
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


def work_version(work_id: UUID, request: Request) -> int:
    value = request.headers.get("If-Match")
    if value is None:
        raise HTTPException(428, "If-Match is required")
    match = re.fullmatch(r'"work-([0-9a-f-]+)-v([1-9][0-9]*)"', value)
    if not match or match.group(1) != str(work_id):
        raise HTTPException(409, "Work version does not match")
    return int(match.group(2))


def parse_work_requirement(value: dict) -> Requirement:
    try:
        return Requirement.from_dict(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/works")
def list_works(
    request: Request, before: UUID | None = None, context: AuthContext = Depends(auth_context)
):
    return request.app.state.works.list(context.account_id, before)


@router.get("/api/v1/works/{work_id}")
def get_work(work_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    body = request.app.state.works.get(context.account_id, work_id)
    response = JSONResponse(content=body)
    response.headers["ETag"] = f'"work-{work_id}-v{body["work"]["row_version"]}"'
    return response


@router.post("/api/v1/works")
def accept_work(
    payload: CreateWorkRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    result = request.app.state.works.accept(
        context.account_id,
        key,
        payload.title,
        parse_work_requirement(payload.requirement),
        conversation_id=payload.conversation_id,
        source_message_id=payload.source_message_id,
    )
    response = work_response(result)
    response.headers["Location"] = f"/api/v1/works/{result.body['work']['work_id']}"
    return response


@router.post("/api/v1/works/{work_id}/revisions")
def revise_work(
    work_id: UUID,
    payload: ReviseWorkRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.revise(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            parse_work_requirement(payload.requirement),
            payload.change_reason,
        )
    )


@router.post("/api/v1/works/{work_id}/pause")
def pause_work(
    work_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.control(
            context.account_id, work_id, key, work_version(work_id, request), "pause"
        )
    )


@router.post("/api/v1/works/{work_id}/resume")
def resume_work(
    work_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.control(
            context.account_id, work_id, key, work_version(work_id, request), "resume"
        )
    )


@router.post("/api/v1/works/{work_id}/stop")
def stop_work(
    work_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.control(
            context.account_id, work_id, key, work_version(work_id, request), "stop"
        )
    )


@router.post("/api/v1/works/{work_id}/advance")
def advance_work(
    work_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.advance(
            context.account_id, work_id, key, work_version(work_id, request)
        )
    )


@router.put("/api/v1/works/{work_id}/conversations/{conversation_id}")
def link_work(
    work_id: UUID,
    conversation_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.link(
            context.account_id, work_id, conversation_id, key, work_version(work_id, request)
        )
    )


@router.get("/api/v1/works/{work_id}/events")
def work_events(
    work_id: UUID,
    request: Request,
    after: int = Query(0, ge=0),
    context: AuthContext = Depends(auth_context),
):
    return request.app.state.works.records(context.account_id, work_id, "events", after)


@router.get("/api/v1/works/{work_id}/runs")
def work_runs(work_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return request.app.state.works.records(context.account_id, work_id, "runs")


@router.get("/api/v1/works/{work_id}/artifacts")
def work_artifacts(work_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return {"items": request.app.state.works.get(context.account_id, work_id)["work"]["artifacts"]}


@router.post("/api/v1/works/{work_id}/artifacts")
def reference_work_artifact(
    work_id: UUID,
    payload: ReferenceWorkArtifactRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "artifact_reference",
            payload.model_dump(),
        )
    )


@router.get("/api/v1/works/{work_id}/notification-targets")
def work_targets(work_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    from work_domain.persistence import WorkRepository, dto

    with UnitOfWork(request.app.state.api_pool) as uow:
        WorkRepository.get(uow, context.account_id, work_id)
        return {
            "items": dto(
                uow.execute(
                    "SELECT target_id,channel,audience,content_scope,target_version,enabled FROM delivery_targets WHERE account_id=%s AND work_id=%s ORDER BY created_at",
                    (context.account_id, work_id),
                ).fetchall()
            )
        }


@router.put("/api/v1/works/{work_id}/notification-targets")
def set_work_target(
    work_id: UUID,
    payload: WorkTargetRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "target",
            payload.model_dump(),
        )
    )


@router.delete("/api/v1/works/{work_id}/notification-targets/{target_id}")
def disable_work_target(
    work_id: UUID,
    target_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "disable_target",
            {"target_id": target_id},
        )
    )


@router.post("/api/v1/works/{work_id}/budget")
def increase_work_budget(
    work_id: UUID,
    payload: WorkBudgetRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "budget",
            payload.model_dump(),
        )
    )


@router.get("/api/v1/works/{work_id}/inputs")
def work_inputs(work_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    from work_domain.persistence import WorkRepository, dto

    with UnitOfWork(request.app.state.api_pool) as uow:
        WorkRepository.get(uow, context.account_id, work_id)
        return {
            "items": dto(
                uow.execute(
                    "SELECT * FROM work_input_refs WHERE account_id=%s AND work_id=%s ORDER BY ref_id",
                    (context.account_id, work_id),
                ).fetchall()
            )
        }


@router.post("/api/v1/works/{work_id}/inputs")
def add_work_input(
    work_id: UUID,
    payload: WorkInputRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "input",
            payload.model_dump(),
        )
    )


@router.delete("/api/v1/works/{work_id}/inputs/{ref_id}")
def revoke_work_input(
    work_id: UUID,
    ref_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "revoke_input",
            {"ref_id": ref_id},
        )
    )


@router.post("/api/v1/works/{work_id}/accept-result")
def accept_work_result(
    work_id: UUID,
    payload: AcceptWorkResultRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "accept_result",
            payload.model_dump(),
        )
    )


@router.get("/api/v1/notifications")
def notification_inbox(
    request: Request, before: UUID | None = None, context: AuthContext = Depends(auth_context)
):
    from work_domain.persistence import dto

    with UnitOfWork(request.app.state.api_pool) as uow:
        rows = uow.execute(
            "SELECT n.*,d.provider_receipt FROM notifications n JOIN deliveries d USING(account_id,notification_id) JOIN delivery_targets t USING(account_id,target_id) WHERE n.account_id=%s AND t.channel='web' AND d.state='accepted' AND (%s::uuid IS NULL OR n.notification_id<%s) ORDER BY n.notification_id DESC LIMIT 100",
            (context.account_id, before, before),
        ).fetchall()
        return {"items": dto(rows)}


@router.post("/api/v1/works/{work_id}/deliveries/{delivery_id}/resolve")
def resolve_work_delivery(
    work_id: UUID,
    delivery_id: UUID,
    payload: ResolveDeliveryRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "resolve_delivery",
            {"delivery_id": delivery_id, **payload.model_dump()},
        )
    )


@router.get("/api/v1/works/{work_id}/events/stream")
async def stream_work_events(
    work_id: UUID,
    request: Request,
    after: int = Query(0, ge=0),
    context: AuthContext = Depends(auth_context),
):
    service = request.app.state.works
    await asyncio.to_thread(service.get, context.account_id, work_id)
    cursor = request.headers.get("Last-Event-ID", str(after))
    if not cursor.isdigit():
        raise HTTPException(422, "invalid Work event cursor")

    async def frames():
        position = int(cursor)
        while not await request.is_disconnected():
            try:
                current = await asyncio.to_thread(auth_context, request)
            except Unauthenticated:
                return
            if current.account_id != context.account_id:
                return
            batch = await asyncio.to_thread(
                service.records, context.account_id, work_id, "events", position
            )
            for event in batch["items"]:
                position = event["event_seq"]
                yield f"id: {position}\nevent: work.event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not batch["items"]:
                yield ": keepalive\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/works/{work_id}/resources")
def work_resources(work_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return {"grants": request.app.state.resource_policy.grants(context.account_id, "work", work_id)}


@router.post("/api/v1/works/{work_id}/resources", status_code=201)
def grant_work_resource(
    work_id: UUID,
    payload: GrantConversationResourceRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "grant_resource",
            payload.model_dump(),
        )
    )


@router.delete("/api/v1/works/{work_id}/resources/{grant_id}")
def revoke_work_resource(
    work_id: UUID,
    grant_id: UUID,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    return work_response(
        request.app.state.works.integration(
            context.account_id,
            work_id,
            key,
            work_version(work_id, request),
            "revoke_resource",
            {"grant_id": grant_id},
        )
    )
