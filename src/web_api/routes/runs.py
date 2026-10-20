from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from persistence.command_result import CommandResult
from research_domain.services import ResearchQueryService
from web_api.auth import AuthContext, AuthService
from web_api.command_projection import command_body
from web_api.dependencies import auth_context, csrf_guard, idempotency_key, session_cookie_name
from web_api.models import EmptyRequest
from web_api.queries import trace_tree_dto
from web_api.responses import error_response
from web_api.sse import SSEGateway, load_run_snapshot
from web_domain.errors import ResourceNotFound

router = APIRouter()


@router.get("/api/v1/runs/{run_id}/research/evidence")
def research_evidence(run_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return ResearchQueryService(request.app.state.api_pool).evidence(context.account_id, run_id)


@router.get("/api/v1/runs/{run_id}/research/report")
def research_report(run_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return ResearchQueryService(request.app.state.api_pool).report(context.account_id, run_id)


@router.get("/api/v1/runs/{run_id}")
def get_run(run_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return request.app.state.queries.get_run(context.account_id, run_id)


@router.get("/api/v1/runs/{run_id}/trace")
def get_run_trace(
    run_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
):
    tree = request.app.state.trace_repository.get_trace_tree(context.account_id, run_id)
    if tree is None:
        raise ResourceNotFound()
    return trace_tree_dto(tree)


@router.get("/api/v1/runs/{run_id}/model-inputs")
def list_run_model_inputs(
    run_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
):
    return request.app.state.model_observability.list_run_model_snapshots(
        context.account_id, run_id
    )


@router.get("/api/v1/model-inputs/{snapshot_id}")
def get_model_input(
    snapshot_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
):
    return request.app.state.model_observability.get_model_snapshot(context.account_id, snapshot_id)


@router.get("/api/v1/runs/{run_id}/events")
async def run_events(
    run_id: UUID,
    request: Request,
    context: AuthContext = Depends(auth_context),
):
    """SSE projection of one Run (hpagent-web-api-contract.md §12, §13).

    Authentication is the same-origin HttpOnly Cookie (no CSRF for GET).
    Connection errors before streaming return JSON; after the stream starts,
    problems are signalled with control events (stream.degraded / auth.expired).
    """
    gateway: SSEGateway = request.app.state.sse_gateway
    # Bound concurrent SSE connections before streaming begins.
    if not await gateway.try_acquire():
        return error_response(
            request,
            503,
            "service_unavailable",
            "SSE 连接数已达上限，请稍后重试。",
            retryable=True,
        )
    # Verify ownership before starting the stream so pre-stream errors can
    # still be non-200 JSON (contract §12.1).
    try:
        await asyncio.to_thread(
            load_run_snapshot, request.app.state.api_pool, context.account_id, run_id
        )
    except Exception as exc:
        await gateway.release()
        if isinstance(exc, ResourceNotFound):
            return error_response(request, 404, "resource_not_found", "资源不存在。")
        raise
    raw_token = request.cookies.get(session_cookie_name(request), "")
    auth_service: AuthService = request.app.state.auth

    def auth_check(raw: str) -> bool:
        return auth_service.authenticate(raw) is not None

    return StreamingResponse(
        gateway.stream(
            context.account_id,
            run_id,
            raw_token,
            auth_check,
            acquired=True,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/v1/runs/{run_id}/cancel")
def cancel_run(
    run_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    result: CommandResult = request.app.state.commands.cancel_run(context.account_id, run_id, key)
    response = JSONResponse(
        status_code=result.response_status,
        content=command_body(result, "run", "assistant_message"),
    )
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.post("/api/v1/runs/{run_id}/retry")
def retry_run(
    run_id: UUID,
    payload: EmptyRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    result: CommandResult = request.app.state.commands.retry_run(context.account_id, run_id, key)
    body = command_body(result, "source_run_id", "assistant_message", "run", events=True)
    response = JSONResponse(status_code=result.response_status, content=body)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    if result.resource_reused:
        response.headers["Resource-Reused"] = "true"
    return response
