from __future__ import annotations

import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory.hindsight_client import HindsightError, HindsightRejected
from web_api.auth import AuthContext
from web_api.dependencies import auth_context, csrf_guard, idempotency_key
from web_api.responses import error_response

router = APIRouter(prefix='/api/v1/memory')


class MemoryActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['correct', 'invalidate', 'restore', 'delete']
    generation: int = Field(gt=0, strict=True)
    memory_id: UUID | None = None
    text: str | None = Field(default=None, min_length=1, max_length=16000)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def valid_action(self):
        if ((self.kind == 'delete') != (self.memory_id is None) or
                (self.kind == 'correct') != (self.text is not None) or
                (self.text is not None and not self.text.strip()) or not self.reason.strip()):
            raise ValueError('invalid memory action')
        return self


@router.get('/sources')
def sources(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
            context: AuthContext = Depends(auth_context)):
    return request.app.state.memory_governance.sources(context.account_id, limit, offset)


@router.get('/sources/{source_id}')
async def source(source_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return await request.app.state.memory_governance.detail(context.account_id, source_id)


@router.post('/sources/{source_id}/actions')
async def command(source_id: UUID, payload: MemoryActionRequest, request: Request,
            context: AuthContext = Depends(csrf_guard), key: str = Depends(idempotency_key)):
    body = payload.model_dump(mode='json', exclude_none=True)
    try:
        await request.app.state.memory_governance.validate_target(context.account_id, source_id, key, body)
    except HindsightRejected as exc:
        status = 404 if exc.status == 404 else 403
        return error_response(request, status, 'memory_target_denied', '事实不在当前授权来源范围内。')
    except HindsightError:
        return error_response(request, 503, 'memory_backend_unavailable', '记忆服务暂时不可用。', retryable=True)
    result = await asyncio.to_thread(request.app.state.memory_governance.command,
                                    context.account_id, source_id, key, body)
    response = JSONResponse(status_code=result.response_status,
                            content=json.loads(json.dumps(result.body, default=str)))
    response.headers['Location'] = '/api/v1/memory/actions/' + result.body['action_id']
    if result.replayed:
        response.headers['Idempotency-Replayed'] = 'true'
    return response


@router.get('/actions/{action_id}')
def action(action_id: UUID, request: Request, context: AuthContext = Depends(auth_context)):
    return request.app.state.memory_governance.action(context.account_id, action_id)
