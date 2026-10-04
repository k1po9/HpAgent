"""Safe model failure categories shared by durable callers."""
from __future__ import annotations

import json
from dataclasses import dataclass

from common.errors import ModelAPIError


class ModelRequestInvalid(ValueError):
    """The local transcript cannot be sent to a provider. Never retry it."""


class ModelResponseInvalid(ValueError):
    """A provider response does not have the expected response shape."""


@dataclass(frozen=True)
class ModelFailure:
    code: str
    safe_message: str
    retryable: bool
    category: str
    http_status: int | None = None


def classify_model_failure(exc: BaseException) -> ModelFailure:
    # Activities classify transport failures; workflow imports only need the
    # exception types and must not initialize HTTP clients inside the sandbox.
    import httpx

    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    for cause in chain:
        if isinstance(cause, ModelRequestInvalid):
            return ModelFailure("model_request_invalid", "模型请求格式校验失败。", False, "request")
        if isinstance(cause, (httpx.ConnectTimeout, httpx.ConnectError, ConnectionError)):
            return ModelFailure("model_connection_failed", "无法连接模型服务。", True, "connection")
        if isinstance(cause, (httpx.TimeoutException, TimeoutError)):
            return ModelFailure("model_read_timeout", "等待模型响应超时。", True, "timeout")
        if isinstance(cause, (ModelResponseInvalid, json.JSONDecodeError, httpx.DecodingError)):
            return ModelFailure("model_response_invalid", "模型响应无法解析。", False, "response")
    for cause in reversed(chain):
        status = (cause.response.status_code if isinstance(cause, httpx.HTTPStatusError)
                  else cause.details.get("status_code") if isinstance(cause, ModelAPIError)
                  else None)
        if status is None:
            continue
        if status in (401, 403):
            return ModelFailure("model_access_denied", "模型服务拒绝访问。", False, "http", status)
        if status in (402, 429):
            return ModelFailure("model_rate_limited", "模型服务额度不足或请求过多。", False, "http", status)
        if 400 <= status < 500:
            return ModelFailure("model_request_rejected", "模型服务拒绝请求。", False, "http", status)
        return ModelFailure("model_http_error", "模型服务返回错误。", True, "http", status)
    if any(isinstance(cause, httpx.RequestError) for cause in chain):
        return ModelFailure("model_connection_failed", "模型连接异常。", True, "connection")
    return ModelFailure("model_unavailable", "模型暂时不可用。", True, "unknown")
