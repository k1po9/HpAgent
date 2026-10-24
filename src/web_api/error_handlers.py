from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.logging import log_event
from file_domain.approvals import ApprovalNotPending
from web_api.dependencies import CsrfInvalid, InvalidIdempotencyKey, Unauthenticated
from web_api.model_observability_queries import ModelInputUnavailable
from web_api.responses import error_response
from web_api.security import CursorError
from web_domain.errors import (
    ConversationBusy,
    DomainError,
    FileAlreadyBound,
    FileEncodingUnsupported,
    FileHashMismatch,
    FileNotReady,
    FileTooLarge,
    FileUploadInvalid,
    IdempotencyConflict,
    ResourceNotFound,
    RunNotCancellable,
    RunNotRetryable,
    RunRetryNotSafe,
    UnsupportedFileType,
    VersionConflict,
)
from work_domain.commands import WorkConflict
from work_domain.models import GENERIC_RESULT_EVIDENCE_TYPES, LegacyGenericCriteria
from work_domain.save_policy import InvalidWorkspaceSave
from workspace.catalog import WorkspaceConflict, WorkspaceNotFound
from workspace.resources import ResourceDenied, SnapshotLimitExceeded

logger = logging.getLogger("HpAgent.WebApi")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidWorkspaceSave)
    async def invalid_workspace_save(request: Request, exc: InvalidWorkspaceSave):
        return error_response(
            request, 422, "workspace_save_invalid",
            "保存要求无效。普通成果无需保存到空间；明确要求保存时，请保留保存验收条件并选择真实目录，未确定时省略目录等待补充。",
            details={"fields": ["requirement.acceptance_criteria", "requirement.deliverable_policy"]},
        )

    @app.exception_handler(LegacyGenericCriteria)
    async def legacy_generic_criteria(request: Request, exc: LegacyGenericCriteria):
        return error_response(
            request, 422, "generic_acceptance_revision_required",
            "通用任务不再支持操作回执验收。请在任务编辑器中确认升级为已发布成果条件，再提交修订。",
            details={"fields": ["requirement.acceptance_criteria"],
                     "supported_result_types": sorted(GENERIC_RESULT_EVIDENCE_TYPES)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if any(item.get("type") == "json_invalid" for item in exc.errors()):
            return error_response(request, 400, "malformed_request", "JSON 请求格式错误。")
        details = {
            "fields": [".".join(str(part) for part in item["loc"][1:]) for item in exc.errors()]
        }
        return error_response(request, 422, "validation_error", "请求字段无效。", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return error_response(request, 404, "resource_not_found", "资源不存在。")
        if exc.status_code == 405:
            return error_response(request, 405, "method_not_allowed", "请求方法不受支持。")
        return error_response(request, exc.status_code, "malformed_request", "请求无法处理。")

    @app.exception_handler(CursorError)
    async def cursor_error(request: Request, exc: CursorError):
        return error_response(
            request, 400, exc.code, "分页游标无效或已过期。", retryable=exc.code == "cursor_expired"
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        mapping: dict[type[DomainError], tuple[int, str, str, bool]] = {
            ResourceNotFound: (404, "resource_not_found", "资源不存在。", False),
            WorkspaceNotFound: (404, "workspace_not_found", "Workspace 资源不存在。", False),
            WorkspaceConflict: (409, "workspace_conflict", "Workspace 操作冲突或名称无效。", False),
            ResourceDenied: (403, "resource_denied", "当前主体或 Run 无权使用该资源。", False),
            SnapshotLimitExceeded: (
                413,
                "candidate_limit_exceeded",
                "候选超过上限，请缩小授权范围。",
                False,
            ),
            ConversationBusy: (409, "conversation_busy", "当前对话仍有请求正在执行。", True),
            FileNotReady: (409, "file_not_ready", "文件尚未准备完成。", True),
            FileAlreadyBound: (409, "file_already_bound", "附件不能重复绑定。", False),
            FileTooLarge: (413, "file_too_large", "文件超过大小限制。", False),
            UnsupportedFileType: (415, "unsupported_file_type", "不支持该文件类型。", False),
            FileEncodingUnsupported: (
                422,
                "file_encoding_unsupported",
                "文件编码不受支持。",
                False,
            ),
            FileHashMismatch: (422, "file_hash_mismatch", "文件哈希校验失败。", False),
            FileUploadInvalid: (409, "file_not_ready", "上传状态无效，请重新创建上传。", False),
            IdempotencyConflict: (409, "idempotency_conflict", "幂等键对应的请求不一致。", False),
            RunNotCancellable: (409, "run_not_cancellable", "当前运行状态不可取消。", False),
            RunNotRetryable: (409, "run_not_retryable", "当前运行状态不可重试。", False),
            RunRetryNotSafe: (
                409,
                "run_retry_not_safe",
                "任务包含无法确认的外部操作，不能自动重试。",
                False,
            ),
            VersionConflict: (412, "version_conflict", "对话版本已经变化。", True),
            WorkConflict: (409, "work_conflict", "Work 状态或版本冲突。", True),
            ApprovalNotPending: (409, "approval_not_pending", "审批请求已处理或已过期。", False),
        }
        status, code, message, retryable = mapping.get(
            type(exc), (500, "service_unavailable", "服务暂不可用。", True)
        )
        details = (
            {"current_version": exc.current_version} if isinstance(exc, VersionConflict) else {}
        )
        if isinstance(exc, WorkspaceConflict):
            details = {"reason": exc.reason}
            message = {
                "name_exists": "目标目录已有同名文件或目录。",
                "invalid_name": "名称无效，请避免斜杠、首尾空格或过长名称。",
                "invalid_parent": "目标目录不存在或不可用。",
                "directory_not_empty": "目录非空，请先移除或移动子项。",
                "workspace_cycle": "不能把目录移动到自身或其子目录。",
                "immutable_node": "此入口不能执行这项修改。",
                "file_unavailable": "文件当前不可用。",
            }.get(exc.reason, message)
        if isinstance(exc, WorkConflict):
            details = {"current": exc.current, "reason": exc.reason}
        if isinstance(exc, RunRetryNotSafe):
            details = {
                "failure_code": exc.failure_code,
                "reason": exc.reason,
            }
            if exc.reason != "unsafe_side_effect_state":
                message = "该失败无法直接重试，请处理原因后重新发起。"
        if code in {"resource_denied", "candidate_limit_exceeded", "workspace_conflict"}:
            log_event(
                logger,
                logging.WARNING,
                "workspace_request_rejected",
                "web_api",
                code=code,
                request_id=getattr(request.state, "request_id", None),
                run_id=request.path_params.get("run_id"),
                node_id=request.path_params.get("node_id"),
                reason=details.get("reason"),
            )
        return error_response(request, status, code, message, retryable=retryable, details=details)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        logger.exception(
            "Unhandled Web API exception",
            extra={
                "event": "web_request_failed",
                "component": "web_api",
                "request_id": getattr(request.state, "request_id", None),
                "run_id": request.path_params.get("run_id"),
                "path": request.url.path,
                "method": request.method,
                "status": "failed",
            },
        )
        return error_response(request, 503, "service_unavailable", "服务暂不可用。", retryable=True)

    @app.exception_handler(Unauthenticated)
    async def unauthenticated(request: Request, exc: Unauthenticated):
        return error_response(request, 401, "unauthenticated", "登录状态无效。", retryable=True)

    @app.exception_handler(CsrfInvalid)
    async def csrf_invalid(request: Request, exc: CsrfInvalid):
        return error_response(request, 403, "csrf_invalid", "CSRF 校验失败。", retryable=True)

    @app.exception_handler(ModelInputUnavailable)
    async def model_input_unavailable(request: Request, exc: ModelInputUnavailable):
        return error_response(request, 403, "model_input_unavailable", "当前账号不可查看模型输入。")

    @app.exception_handler(InvalidIdempotencyKey)
    async def invalid_key(request: Request, exc: InvalidIdempotencyKey):
        return error_response(request, 422, "validation_error", "Idempotency-Key 必须是规范 UUID。")
