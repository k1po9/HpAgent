from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from common.logging import log_event
from persistence.command_result import CommandResult
from web_api.auth import AuthContext
from web_api.command_projection import command_body
from web_api.dependencies import auth_context, csrf_guard, idempotency_key
from web_api.models import CreateConversationRequest, RenameConversationRequest, SendMessageRequest
from web_api.responses import error_response

logger = logging.getLogger("HpAgent.WebApi")

router = APIRouter()

ETAG_PATTERN = re.compile(r'^"conversation-([0-9a-f-]+)-m([1-9][0-9]*)"$')


def _etag(conversation: dict[str, Any]) -> str:
    return f'"conversation-{conversation["conversation_id"]}-m{conversation["metadata_version"]}"'


def _normalize_title(value: str | None) -> str:
    if value is None:
        return "新的对话"
    title = value.strip()
    if not title or len(title) > 200:
        raise ValueError("invalid title")
    return title


def _normalize_content(value: str) -> str:
    content = value.replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        raise EmptyMessage()
    if len(content) > 32_000 or len(content.encode("utf-8")) > 128 * 1024:
        raise MessageTooLarge()
    return content


class EmptyMessage(Exception):
    pass


class MessageTooLarge(Exception):
    pass


@router.post("/api/v1/conversations")
def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    try:
        title = _normalize_title(payload.title)
    except ValueError:
        return error_response(request, 422, "validation_error", "标题无效。")
    result: CommandResult = request.app.state.commands.create_conversation(
        context.account_id, key, title
    )
    response = JSONResponse(
        status_code=result.response_status, content={"conversation": result.body["conversation"]}
    )
    response.headers["Location"] = (
        f"/api/v1/conversations/{result.body['conversation']['conversation_id']}"
    )
    response.headers["ETag"] = _etag(result.body["conversation"])
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


@router.get("/api/v1/conversations")
def list_conversations(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
    context: AuthContext = Depends(auth_context),
):
    return request.app.state.queries.list_conversations(context.account_id, limit, cursor)


@router.get("/api/v1/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID, request: Request, context: AuthContext = Depends(auth_context)
):
    result = request.app.state.queries.get_conversation(context.account_id, conversation_id)
    response = JSONResponse(content=json.loads(json.dumps(result, default=str)))
    response.headers["ETag"] = _etag(result["conversation"])
    return response


@router.patch("/api/v1/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: UUID,
    payload: RenameConversationRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    if_match: str | None = Header(None, alias="If-Match"),
):
    if not if_match:
        return error_response(
            request, 428, "precondition_required", "缺少 If-Match。", retryable=True
        )
    match = ETAG_PATTERN.fullmatch(if_match)
    if not match or match.group(1) != str(conversation_id):
        return error_response(
            request, 412, "version_conflict", "ETag 与资源不匹配。", retryable=True
        )
    try:
        title = _normalize_title(payload.title)
    except ValueError:
        return error_response(request, 422, "validation_error", "标题无效。")
    conversation = request.app.state.queries.rename_conversation(
        context.account_id, conversation_id, int(match.group(2)), title
    )
    response = JSONResponse(content={"conversation": conversation})
    response.headers["ETag"] = _etag(conversation)
    return response


@router.get("/api/v1/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: UUID,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    context: AuthContext = Depends(auth_context),
):
    return request.app.state.queries.list_messages(
        context.account_id, conversation_id, limit, cursor
    )


@router.post("/api/v1/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: UUID,
    payload: SendMessageRequest,
    request: Request,
    context: AuthContext = Depends(csrf_guard),
    key: str = Depends(idempotency_key),
):
    if payload.file_ids and not request.app.state.settings.web_file_upload_enabled:
        return error_response(request, 409, "file_capability_disabled", "文件能力尚未启用。")
    if len(payload.file_ids) > request.app.state.settings.file_max_count_per_message:
        return error_response(request, 422, "file_processing_limit", "附件数量超过限制。")
    try:
        content = _normalize_content(payload.content)
    except EmptyMessage:
        return error_response(request, 422, "empty_message", "消息不能为空。")
    except MessageTooLarge:
        return error_response(request, 413, "message_too_large", "消息过长。")
    result: CommandResult = request.app.state.commands.send_message(
        context.account_id,
        conversation_id,
        key,
        content,
        payload.agent_strategy,
        tuple(payload.file_ids),
    )
    run = result.body["run"]
    log_event(
        logger,
        logging.INFO,
        "web_message_accepted",
        "web_api",
        request_id=request.state.request_id,
        run_id=run["run_id"],
        conversation_id=str(conversation_id),
        status="success",
    )
    body = command_body(result, "user_message", "assistant_message", "run", events=True)
    response = JSONResponse(status_code=result.response_status, content=body)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response
